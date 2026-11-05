"""Churn prediction pipeline with multi-family model comparison.

Label: a buyer is "churned" when their recency (days since last order as of the
organization's latest order date, already stored in Customer360 features)
exceeds a churn window (default 90 days). Features are the same Customer 360
metrics used by segmentation.

Training trains every requested family on the same stratified split, evaluates
each on the held-out test set, persists one ModelVersion per family under a
single "Churn Prediction" Model, and activates the best champion (best ROC-AUC,
tie-break PR-AUC). Scoring uses the active version and writes Prediction rows.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import AuditAction, RiskLevel
from app.core.exceptions import InsufficientDataError
from app.models.customer_features_model import CustomerFeature, Model, ModelVersion, Prediction
from app.models.dataset import DatasetVersion
from app.models.transaction import Customer
from app.services.audit_service import audit

MIN_SAMPLES_FOR_TRAINING = 16

CHURN_FEATURES = [
    "recency_days",
    "frequency",
    "monetary",
    "aov",
    "avg_days_between",
    "tenure_days",
    "category_diversity",
    "discount_usage",
    "engagement_score",
    "clv_est",
]

MODEL_NAME = "Churn Prediction"


def _xgboost_factory(scale_pos_weight: float, random_seed: int):
    import xgboost  # noqa: PLC0415 — optional dependency

    return xgboost.XGBClassifier(
        n_estimators=300,
        max_depth=6,
        learning_rate=0.05,
        scale_pos_weight=scale_pos_weight,
        eval_metric="logloss",
        random_state=random_seed,
        n_jobs=-1,
    )


def _lightgbm_factory(scale_pos_weight: float, random_seed: int):
    import lightgbm  # noqa: PLC0415 — optional dependency

    return lightgbm.LGBMClassifier(
        n_estimators=300,
        learning_rate=0.05,
        scale_pos_weight=scale_pos_weight,
        random_state=random_seed,
        verbose=-1,
        n_jobs=-1,
    )


def _build_classifier(family: str, scale_pos_weight: float, random_seed: int):
    if family == "logistic":
        return LogisticRegression(max_iter=2000, class_weight="balanced", random_state=random_seed)
    if family == "random_forest":
        return RandomForestClassifier(
            n_estimators=300, class_weight="balanced", random_state=random_seed, n_jobs=-1
        )
    if family == "xgboost":
        return _xgboost_factory(scale_pos_weight, random_seed)
    if family == "lightgbm":
        return _lightgbm_factory(scale_pos_weight, random_seed)
    raise ValueError(f"Unknown churn model family: {family}")


AVAILABLE_FAMILIES = ("logistic", "random_forest", "xgboost", "lightgbm")


class ArtifactDir:
    """Per-org/per-model/per-version artifact directory (mirrors segmentation)."""

    def __init__(self, organization_id: int, model_id: int, version_no: int) -> None:
        self.path = (
            Path(settings.model_artifact_dir) / str(organization_id) / str(model_id) / f"v{version_no}"
        )

    def ensure(self) -> None:
        self.path.mkdir(parents=True, exist_ok=True)


def _latest_features(db: Session, organization_id: int) -> list[CustomerFeature]:
    latest = (
        select(CustomerFeature.customer_id, func.max(CustomerFeature.id).label("latest_id"))
        .where(CustomerFeature.organization_id == organization_id)
        .group_by(CustomerFeature.customer_id)
        .subquery()
    )
    return (
        db.execute(
            select(CustomerFeature).join(latest, latest.c.latest_id == CustomerFeature.id)
        )
        .scalars()
        .all()
    )


def _latest_clv_map(db: Session, organization_id: int, customer_ids: list[int]) -> dict[int, float]:
    """Map customer_id -> latest clv_est, restricted to the given customers."""
    if not customer_ids:
        return {}
    latest = (
        select(CustomerFeature.customer_id, func.max(CustomerFeature.id).label("latest_id"))
        .where(
            CustomerFeature.organization_id == organization_id,
            CustomerFeature.customer_id.in_(customer_ids),
        )
        .group_by(CustomerFeature.customer_id)
        .subquery()
    )
    return {
        row.customer_id: float(row.clv_est)
        for row in db.execute(select(CustomerFeature).join(latest, latest.c.latest_id == CustomerFeature.id)).scalars()
        if row.clv_est is not None
    }


def _matrix(features: list[CustomerFeature]) -> tuple[np.ndarray, list[int]]:
    rows = []
    customer_ids: list[int] = []
    for feature in features:
        if not feature.frequency or feature.frequency <= 0:
            continue
        customer_ids.append(feature.customer_id)
        rows.append(
            [
                np.nan if (value := feature.__getattribute__(name)) is None else float(value)
                for name in CHURN_FEATURES
            ]
        )
    matrix = np.asarray(rows, dtype=float)
    for column in range(matrix.shape[1]):
        if matrix.shape[0] and np.isnan(matrix[:, column]).all():
            matrix[:, column] = 0.0
    return matrix, customer_ids


def _classification_metrics(y_true: np.ndarray, y_pred: np.ndarray, y_proba: np.ndarray) -> dict:
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    metrics = {
        "accuracy": round(accuracy_score(y_true, y_pred), 4),
        "precision": round(float(precision_score(y_true, y_pred, zero_division=0)), 4),
        "recall": round(float(recall_score(y_true, y_pred, zero_division=0)), 4),
        "f1": round(float(f1_score(y_true, y_pred, zero_division=0)), 4),
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
    }
    if len(np.unique(y_true)) > 1:
        metrics["roc_auc"] = round(float(roc_auc_score(y_true, y_proba)), 4)
        metrics["pr_auc"] = round(float(average_precision_score(y_true, y_proba)), 4)
    else:
        metrics["roc_auc"] = None
        metrics["pr_auc"] = None
    return metrics


def _score_metric(value: float | None) -> float:
    return float(value) if value is not None else 0.0


def _upsert_model_row(db: Session, organization_id: int, created_by: int | None) -> Model:
    model = db.execute(
        select(Model).where(
            Model.organization_id == organization_id,
            Model.model_type == "churn",
            Model.name == MODEL_NAME,
        )
    ).scalar_one_or_none()
    if model is None:
        model = Model(
            organization_id=organization_id,
            name=MODEL_NAME,
            model_type="churn",
            family="binary_classification",
            status="TRAINED",
            description="Churn propensity trained on Customer 360 features.",
            created_by=created_by,
        )
        db.add(model)
        db.flush()
    return model


def _next_version_no(db: Session, model_id: int, organization_id: int) -> int:
    current = db.execute(
        select(func.max(ModelVersion.version_no)).where(
            ModelVersion.model_id == model_id, ModelVersion.organization_id == organization_id
        )
    ).scalar()
    return int(current or 0) + 1


def _latest_dataset_version_id(db: Session, organization_id: int) -> int | None:
    return db.execute(
        select(DatasetVersion.id)
        .where(DatasetVersion.organization_id == organization_id, DatasetVersion.processed_at.is_not(None))
        .order_by(DatasetVersion.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _risk_level(probability: float) -> str:
    """Map a churn probability to LOW/MEDIUM/HIGH/CRITICAL from thresholds."""
    ordered = sorted(settings.churn_risk_thresholds.items(), key=lambda item: item[1])
    for level, ceiling in ordered:
        if probability <= ceiling:
            return level
    return RiskLevel.CRITICAL


def _median_clv(db: Session, organization_id: int) -> float:
    """Median of the latest CLV estimates across buyers (0.0 when none exist)."""
    latest = (
        select(CustomerFeature.customer_id, func.max(CustomerFeature.id).label("latest_id"))
        .where(CustomerFeature.organization_id == organization_id)
        .group_by(CustomerFeature.customer_id)
        .subquery()
    )
    values = [
        float(row[0])
        for row in db.execute(
            select(CustomerFeature.clv_est)
            .join(latest, latest.c.latest_id == CustomerFeature.id)
            .where(CustomerFeature.clv_est.is_not(None))
        ).all()
        if row[0] is not None
    ]
    return sorted(values)[len(values) // 2] if values else 0.0


def categorize_risk(risk_level: str | None, clv_est: float | None, median_clv: float) -> str:
    """CLV x churn matrix quadrant combining churn risk with customer value."""
    high_risk = risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)
    high_value = clv_est is not None and clv_est >= median_clv
    if high_risk and high_value:
        return "PRIORITY"
    if high_risk:
        return "WIN_BACK"
    if high_value:
        return "GROW"
    return "MONITOR"


def train_churn(
    db: Session,
    organization_id: int,
    *,
    families: list[str] | None = None,
    churn_window_days: int | None = None,
    random_seed: int = 42,
    created_by: int | None = None,
) -> dict:
    window = churn_window_days or settings.churn_window_days
    selected = [f for f in (families or list(AVAILABLE_FAMILIES)) if f in AVAILABLE_FAMILIES]

    features = _latest_features(db, organization_id)
    x, customer_ids = _matrix(features)
    if len(x) < MIN_SAMPLES_FOR_TRAINING:
        raise InsufficientDataError(
            f"Not enough buyer data to train a churn model (need ≥{MIN_SAMPLES_FOR_TRAINING}, "
            "processing a dataset builds features automatically)."
        )

    recency = np.asarray([float(f.recency_days) for f in features if (f.frequency or 0) > 0], dtype=float)
    y = np.asarray([1 if r > window else 0 for r in recency], dtype=int)

    if len(set(y)) < 2:
        raise InsufficientDataError(
            f"Churn label needs both active and churned buyers within a {window}-day window."
        )

    x_train, x_test, y_train, y_test = train_test_split(
        x, y, test_size=0.2, stratify=y, random_state=random_seed
    )

    imputer = SimpleImputer(strategy="median").fit(x_train)
    scaler = StandardScaler().fit(imputer.transform(x_train))

    n_neg = int(np.sum(y_train == 0))
    n_pos = int(np.sum(y_train == 1))
    scale_pos_weight = n_neg / n_pos if n_pos and n_neg else 1.0

    model = _upsert_model_row(db, organization_id, created_by)

    trained: list[dict] = []
    best: tuple[float, float] = (-1.0, -1.0)
    for family in selected:
        version_no = _next_version_no(db, model.id, organization_id)
        artifact = ArtifactDir(organization_id, model.id, version_no)
        artifact.ensure()

        classifier = _build_classifier(family, scale_pos_weight, random_seed)
        classifier.fit(scaler.transform(imputer.transform(x_train)), y_train)

        x_test_prepared = scaler.transform(imputer.transform(x_test))
        y_prob = classifier.predict_proba(x_test_prepared)[:, 1]
        y_pred = (y_prob >= 0.5).astype(int)
        metrics = _classification_metrics(y_test, y_pred, y_prob)

        version = ModelVersion(
            organization_id=organization_id,
            model_id=model.id,
            version_no=version_no,
            artifact_path=str(artifact.path),
            preprocessing_path=str(artifact.path),
            features_list=CHURN_FEATURES,
            params_json={
                "family": family,
                "churn_window_days": window,
                "scale_pos_weight": round(scale_pos_weight, 4),
                "label": "recency > window",
                "random_seed": random_seed,
            },
            metrics_json=metrics,
            train_dataset_version_id=_latest_dataset_version_id(db, organization_id),
            random_seed=random_seed,
            training_duration_s=None,
            trained_at=datetime.now(UTC),
            active=False,
            created_by=created_by,
        )
        db.add(version)
        db.flush()

        joblib.dump(
            {
                "imputer": imputer,
                "scaler": scaler,
                "model": classifier,
                "feature_columns": CHURN_FEATURES,
                "family": family,
                "churn_window_days": window,
                "trained_at": datetime.now(UTC).isoformat(),
                "metrics": metrics,
            },
            artifact.path / "model.joblib",
        )

        score_pair = (metrics.get("roc_auc") or 0.0, metrics.get("pr_auc") or 0.0)
        if score_pair > best:
            best = score_pair

        trained.append(
            {
                "family": family,
                "version_no": version_no,
                "model_version_id": version.id,
                **metrics,
            }
        )

    champion_version_id = max(trained, key=lambda v: (v.get("roc_auc") or 0.0, v.get("pr_auc") or 0.0))[
        "model_version_id"
    ]
    db.execute(
        ModelVersion.__table__.update()
        .where(ModelVersion.model_id == model.id, ModelVersion.organization_id == organization_id)
        .values(active=False)
    )
    db.flush()
    db.execute(
        ModelVersion.__table__.update()
        .where(ModelVersion.id == champion_version_id)
        .values(active=True)
    )
    db.commit()

    champion = next(v for v in trained if v["model_version_id"] == champion_version_id)
    audit(
        db,
        organization_id,
        created_by,
        AuditAction.MODEL_TRAIN,
        entity_type="ml-model",
        entity_id=model.id,
        metadata_json={
            "model_type": "churn",
            "families": selected,
            "champion_family": champion["family"],
            "champion_roc_auc": champion.get("roc_auc"),
            "n_samples": len(x),
            "window_days": window,
        },
    )

    return {
        "status": "TRAINED",
        "model_id": model.id,
        "champion": {
            "family": champion["family"],
            "model_version_id": champion_version_id,
            "version_no": champion["version_no"],
            "metrics": {k: v for k, v in champion.items() if k not in ("family", "version_no", "model_version_id")},
        },
        "n_samples": len(x),
        "window_days": window,
        "families": trained,
        "trained_at": datetime.now(UTC).isoformat(),
    }


def active_churn_version(db: Session, organization_id: int) -> ModelVersion | None:
    model_id = db.execute(
        select(Model.id).where(
            Model.organization_id == organization_id, Model.model_type == "churn"
        )
    ).scalar_one_or_none()
    if model_id is None:
        return None
    return db.execute(
        select(ModelVersion).where(
            ModelVersion.organization_id == organization_id,
            ModelVersion.model_id == model_id,
            ModelVersion.active.is_(True),
        )
        .order_by(ModelVersion.version_no.desc())
        .limit(1)
    ).scalar_one_or_none()


def score_customers(
    db: Session,
    organization_id: int,
    *,
    customer_ids: list[int] | None = None,
) -> dict:
    version = active_churn_version(db, organization_id)
    if version is None:
        raise InsufficientDataError(
            "No active churn model for this organization. Train one first (POST /models/churn/train)."
        )

    bundle = joblib.load(Path(version.artifact_path) / "model.joblib")
    imputer: SimpleImputer = bundle["imputer"]
    scaler: StandardScaler = bundle["scaler"]
    classifier = bundle["model"]

    features = _latest_features(db, organization_id)
    if customer_ids is not None:
        wanted = set(customer_ids)
        features = [f for f in features if (f.frequency or 0) > 0 and f.customer_id in wanted]

    x, ids = _matrix(features)
    if len(x) == 0:
        return {**{"model_id": version.model_id, "version_no": version.version_no, "n_scored": 0, "items": []}}

    probabilities = classifier.predict_proba(scaler.transform(imputer.transform(x)))[:, 1]

    existing = db.execute(
        select(Prediction).where(
            Prediction.organization_id == organization_id,
            Prediction.model_version_id == version.id,
            Prediction.customer_id.in_(ids),
        )
    ).scalars().all()
    for row in existing:
        db.delete(row)
    db.flush()

    items = []
    for customer_id, probability in zip(ids, probabilities, strict=True):
        proba = round(float(probability), 4)
        risk = _risk_level(proba)
        items.append({"customer_id": customer_id, "probability": proba, "risk_level": risk})
        db.add(
            Prediction(
                organization_id=organization_id,
                model_version_id=version.id,
                customer_id=customer_id,
                prediction_label=bool(proba >= 0.5),
                prediction_proba=proba,
                risk_level=risk,
                created_at=datetime.now(UTC),
                inferred_at=datetime.now(UTC),
            )
        )
    db.commit()

    return {
        "model_id": version.model_id,
        "model_version_id": version.id,
        "version_no": version.version_no,
        "n_scored": len(items),
        "items": items,
    }


def at_risk_customers(
    db: Session,
    organization_id: int,
    *,
    min_risk_level: str = RiskLevel.HIGH,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    version = active_churn_version(db, organization_id)
    if version is None:
        return {"items": [], "total": 0, "model_version_id": None}

    orders = {level: i for i, level in enumerate(RiskLevel.ALL)}
    base = select(Prediction).where(
        Prediction.organization_id == organization_id,
        Prediction.model_version_id == version.id,
        Prediction.risk_level.is_not(None),
    )
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar() or 0

    rows = db.execute(
        select(Prediction, Customer)
        .join(Customer, Customer.id == Prediction.customer_id)
        .where(
            Prediction.organization_id == organization_id,
            Prediction.model_version_id == version.id,
            Prediction.risk_level.is_not(None),
        )
        .order_by(Prediction.prediction_proba.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    customer_ids = [customer.id for _, customer in rows]
    clv_values = _latest_clv_map(db, organization_id, customer_ids)
    median_clv = _median_clv(db, organization_id)

    items = []
    total_value_at_risk = 0.0
    by_level: dict[str, int] = {level: 0 for level in RiskLevel.ALL}
    for prediction, customer in rows:
        if orders.get(prediction.risk_level, 0) < orders.get(min_risk_level, orders["HIGH"]):
            continue
        clv_est = clv_values.get(customer.id)
        value_at_risk = round(prediction.prediction_proba * clv_est, 2) if clv_est is not None else None
        category = categorize_risk(prediction.risk_level, clv_est, median_clv)
        by_level[prediction.risk_level] = by_level.get(prediction.risk_level, 0) + 1
        items.append(
            {
                "prediction_id": prediction.id,
                "customer_id": customer.id,
                "name": customer.name or customer.external_customer_id or customer.email or f"#{customer.id}",
                "email": customer.email,
                "external_customer_id": customer.external_customer_id,
                "probability": round(float(prediction.prediction_proba), 4),
                "risk_level": prediction.risk_level,
                "risk_category": category,
                "clv_est": clv_est,
                "value_at_risk": value_at_risk,
            }
        )
        total_value_at_risk += value_at_risk or 0.0

    return {
        "items": items,
        "total": total,
        "model_version_id": version.id,
        "min_risk_level": min_risk_level,
        "median_clv": round(median_clv, 2),
        "summary": {
            "total_value_at_risk": round(total_value_at_risk, 2),
            "by_risk_level": {k: by_level[k] for k in RiskLevel.ALL},
        },
    }