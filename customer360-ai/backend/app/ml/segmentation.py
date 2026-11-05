"""K-Means customer segmentation with PCA + automatic cluster-count selection.

Pipeline: latest Customer 360 features -> median impute -> StandardScaler ->
optional PCA -> K-Means. Model and preprocessing artifacts are persisted with
joblib, a ModelVersion is registered, ML segments are written to `segments`
(type='ML') and every buyer is assigned to exactly one cluster.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import AuditAction
from app.core.exceptions import InsufficientDataError
from app.models.customer_features_model import (
    CustomerFeature,
    CustomerSegment,
    Model,
    ModelVersion,
    Segment,
)
from app.models.dataset import DatasetVersion
from app.models.transaction import Customer, Product, Transaction
from app.services.audit_service import audit

ML_SEGMENT_COLORS = [
    "#0f766e",
    "#b45309",
    "#1d4ed8",
    "#be185d",
    "#7c3aed",
    "#059669",
    "#ea580c",
    "#db2777",
]

MIN_BUYERS_FOR_TRAINING = 8
MAX_CLUSTERS = 8

FEATURE_METRICS = [
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

# (feature, label for high value, label for low value)
PROFILE_MAP = [
    ("recency_days", "lapsed", "active"),
    ("frequency", "frequent", "sparse"),
    ("monetary", "high spend", "low spend"),
    ("aov", "large baskets", "small baskets"),
    ("avg_days_between", "irregular", "regular"),
    ("tenure_days", "long-tenured", "recent"),
    ("category_diversity", "multi-category", "single-category"),
    ("discount_usage", "discount-driven", "full-price"),
    ("engagement_score", "engaged", "disengaged"),
    ("clv_est", "high lifetime value", "low lifetime value"),
]


class ArtifactDir:
    """Compute the per-org/per-model artifact directory."""

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


def _build_matrix(features: list[CustomerFeature]) -> tuple[np.ndarray, list[int], list[str]]:
    customer_ids: list[int] = []
    rows: list[dict] = []
    for f in features:
        if not f.frequency or f.frequency <= 0:
            continue
        customer_ids.append(f.customer_id)
        rows.append(
            {
                "recency_days": f.recency_days,
                "frequency": float(f.frequency),
                "monetary": f.monetary,
                "aov": f.aov,
                "avg_days_between": f.avg_days_between,
                "tenure_days": f.tenure_days,
                "category_diversity": f.category_diversity,
                "discount_usage": f.discount_usage,
                "engagement_score": f.engagement_score,
                "clv_est": f.clv_est,
            }
        )
    frame = pd.DataFrame(rows, columns=FEATURE_METRICS)
    numeric = frame.astype(float)
    imputed = numeric.fillna(numeric.median())
    constant_cols = [
        column for column in imputed.columns if imputed[column].nunique(dropna=False) <= 1
    ]
    selected = [column for column in imputed.columns if column not in constant_cols]
    return imputed[selected].to_numpy(dtype=float), customer_ids, selected


def _build_prep(n_features_after_drop: int, n_samples: int, use_pca: bool) -> tuple[StandardScaler, PCA | None]:
    scaler = StandardScaler()
    if use_pca and n_features_after_drop > 4:
        n_components = max(2, min(4, n_samples - 1, n_features_after_drop))
        return scaler, PCA(n_components=n_components, random_state=42)
    return scaler, None


def _transform(prep, pca, x: np.ndarray) -> tuple[np.ndarray, np.ndarray | None]:
    scaled = prep.fit_transform(x)
    if pca is not None:
        return scaled, pca.fit_transform(scaled)
    return scaled, None


def _select_k(
    x_scaled: np.ndarray,
    x_reduced: np.ndarray | None,
    random_seed: int,
) -> tuple[int, KMeans, float]:
    x = x_reduced if x_reduced is not None else x_scaled
    n = len(x)
    candidates = list(range(2, min(MAX_CLUSTERS, n - 1) + 1))
    best_k, best_score, best_model = candidates[0], -1.0, None
    for k in candidates:
        model = KMeans(n_clusters=k, n_init="auto", random_state=random_seed)
        model.fit(x)
        score = silhouette_score(x, model.labels_) if k > 1 else -1.0
        if score > best_score:
            best_k, best_score, best_model = k, score, model
    if best_model is None:
        raise InsufficientDataError("Not enough buyers to form meaningful segments.")
    return best_k, best_model, best_score


def _latest_dataset_version_id(db: Session, organization_id: int) -> int | None:
    return db.execute(
        select(DatasetVersion.id)
        .where(
            DatasetVersion.organization_id == organization_id,
            DatasetVersion.processed_at.isnot(None),
        )
        .order_by(DatasetVersion.id.desc())
    ).scalars().first()


def _upsert_model_row(db: Session, organization_id: int, created_by: int | None) -> Model:
    model = db.execute(
        select(Model)
        .where(
            Model.organization_id == organization_id,
            Model.model_type == "segmentation",
        )
    ).scalars().first()
    if model is None:
        model = Model(
            organization_id=organization_id,
            name="K-Means Segmentation",
            model_type="segmentation",
            family="kmeans",
            status="TRAINED",
            created_by=created_by,
        )
        db.add(model)
        db.flush()
    return model


def _next_version_no(db: Session, model_id: int, organization_id: int) -> int:
    current = db.execute(
        select(func.max(ModelVersion.version_no)).where(
            ModelVersion.model_id == model_id,
            ModelVersion.organization_id == organization_id,
        )
    ).scalar()
    return (current or 0) + 1


def _profile_text(x_scaled_centroids: np.ndarray, feature_columns: list[str]) -> list[str]:
    label_by_feature = {feature: (low, high) for feature, high, low in PROFILE_MAP}
    profile_pairs = [label_by_feature[column] for column in feature_columns]
    profiles: list[str] = []
    for centroid in x_scaled_centroids:
        ranked = sorted(
            (
                (float(centroid[idx]), low, high)
                for idx, (low, high) in enumerate(profile_pairs)
            ),
            key=lambda item: abs(item[0]),
            reverse=True,
        )
        traits = [
            high if value > 0 else low
            for value, low, high in ranked[:3]
            if abs(value) >= 0.30
        ]
        profiles.append(" · ".join(traits[:3]) if traits else "Balanced profile")
    return profiles


def train_segmentation(
    db: Session,
    organization_id: int,
    *,
    n_clusters: int | None = None,
    use_pca: bool = True,
    random_seed: int = 42,
    created_by: int | None = None,
) -> dict:
    """Train (or retrain) K-Means segmentation for an org's buyers."""
    features = _latest_features(db, organization_id)
    x, customer_ids, feature_columns = _build_matrix(features)
    if len(x) < MIN_BUYERS_FOR_TRAINING:
        raise InsufficientDataError(
            "Not enough buyer data to train segments. Process more transactions first."
        )

    n_samples, n_features = x.shape
    prep, pca = _build_prep(n_features, n_samples, use_pca)
    x_scaled, x_reduced = _transform(prep, pca, x)

    if n_clusters is not None and not 2 <= n_clusters <= min(MAX_CLUSTERS, n_samples - 1):
        raise InsufficientDataError(
            f"n_clusters must be between 2 and {min(MAX_CLUSTERS, n_samples - 1)} for this data."
        )

    if n_clusters is None:
        k, kmeans, silhouette = _select_k(x_scaled, x_reduced, random_seed)
        auto_selected = True
    else:
        x = x_reduced if x_reduced is not None else x_scaled
        kmeans = KMeans(n_clusters=n_clusters, n_init="auto", random_state=random_seed)
        kmeans.fit(x)
        k = n_clusters
        silhouette = float(silhouette_score(x, kmeans.labels_)) if k > 1 else -1.0
        auto_selected = False

    model = _upsert_model_row(db, organization_id, created_by)
    version_no = _next_version_no(db, model.id, organization_id)
    artifact = ArtifactDir(organization_id, model.id, version_no)
    artifact.ensure()

    scaled_centroids = np.vstack(
        [x_scaled[kmeans.labels_ == i].mean(axis=0) for i in range(k)]
    )
    profiles = _profile_text(scaled_centroids, feature_columns)

    members_per_cluster = [0] * k
    for label in kmeans.labels_:
        members_per_cluster[int(label)] += 1

    model_version = ModelVersion(
        organization_id=organization_id,
        model_id=model.id,
        version_no=version_no,
        artifact_path=str(artifact.path),
        preprocessing_path=str(artifact.path),
        features_list=feature_columns,
        params_json={
            "n_clusters": k,
            "use_pca": use_pca and pca is not None,
            "n_components": pca.n_components if pca is not None else n_features,
            "random_seed": random_seed,
            "auto_select": auto_selected,
        },
        metrics_json={
            "silhouette_score": round(silhouette, 4) if silhouette >= 0 else None,
            "inertia": round(float(kmeans.inertia_), 2),
            "n_samples": n_samples,
            "members_per_cluster": members_per_cluster,
        },
        train_dataset_version_id=_latest_dataset_version_id(db, organization_id),
        random_seed=random_seed,
        trained_at=datetime.now(UTC),
        active=True,
        created_by=created_by,
    )
    db.add(model_version)
    db.flush()

    db.execute(
        ModelVersion.__table__.update()
        .where(
            ModelVersion.model_id == model.id,
            ModelVersion.organization_id == organization_id,
            ModelVersion.id != model_version.id,
        )
        .values(active=False)
    )

    joblib.dump(
        {
            "prep": prep,
            "pca": pca,
            "feature_columns": feature_columns,
            "model": kmeans,
            "n_clusters": k,
            "trained_at": datetime.now(UTC).isoformat(),
            "metrics": {
                "silhouette_score": round(silhouette, 4) if silhouette >= 0 else None,
                "inertia": round(float(kmeans.inertia_), 2),
            },
        },
        artifact.path / "model.joblib",
    )

    segment_rows: list[Segment] = []
    for i in range(k):
        segment = db.execute(
            select(Segment).where(
                Segment.organization_id == organization_id,
                Segment.type == "ML",
                Segment.name == f"Cluster {i}",
            )
        ).scalar_one_or_none()
        if segment is None:
            segment = Segment(organization_id=organization_id, type="ML", name=f"Cluster {i}", active=True)
            db.add(segment)
            db.flush()
        segment.description = profiles[i]
        segment.color = ML_SEGMENT_COLORS[i % len(ML_SEGMENT_COLORS)]
        segment.rules_json = {
            "cluster_index": i,
            "size": members_per_cluster[i],
            "model_version_id": model_version.id,
        }
        segment_rows.append(segment)
    db.flush()
    segment_ids = {i: segment_rows[i].id for i in range(k)}

    ml_segment_ids = {s.id for s in segment_rows}
    existing_assignments = db.execute(
        select(CustomerSegment).where(
            CustomerSegment.organization_id == organization_id,
            CustomerSegment.segment_id.in_(ml_segment_ids),
        )
    ).scalars().all()
    for assignment in existing_assignments:
        db.delete(assignment)
    db.flush()

    for customer_id, label in zip(customer_ids, kmeans.labels_, strict=True):
        db.add(
            CustomerSegment(
                organization_id=organization_id,
                customer_id=customer_id,
                segment_id=segment_ids[int(label)],
            )
        )
    db.commit()

    audit(
        db,
        organization_id,
        created_by,
        AuditAction.MODEL_TRAIN,
        entity_type="ml-model",
        entity_id=model.id,
        metadata_json={
            "model_type": "segmentation",
            "version_no": version_no,
            "n_clusters": k,
            "silhouette_score": round(silhouette, 4) if silhouette >= 0 else None,
        },
    )

    return {
        "status": "TRAINED",
        "model_id": model.id,
        "model_version_id": model_version.id,
        "version_no": version_no,
        "n_clusters": k,
        "n_samples": n_samples,
        "silhouette_score": round(silhouette, 4) if silhouette >= 0 else None,
        "inertia": round(float(kmeans.inertia_), 2),
        "auto_selected": auto_selected,
        "segments": [
            {"name": f"Cluster {i}", "size": members_per_cluster[i], "profile": profiles[i]}
            for i in range(k)
        ],
        "trained_at": datetime.now(UTC).isoformat(),
    }


def _segment_member_ids(db: Session, organization_id: int, segment_id: int) -> list[int]:
    return list(
        db.execute(
            select(CustomerSegment.customer_id).where(
                CustomerSegment.organization_id == organization_id,
                CustomerSegment.segment_id == segment_id,
            )
        ).scalars().all()
    )


def segment_analytics(db: Session, organization_id: int, segment_id: int) -> dict | None:
    segment = db.execute(
        select(Segment).where(
            Segment.id == segment_id,
            Segment.organization_id == organization_id,
        )
    ).scalar_one_or_none()
    if segment is None:
        return None

    member_ids = _segment_member_ids(db, organization_id, segment_id)
    if not member_ids:
        return {
            "segment": {
                "id": segment.id,
                "name": segment.name,
                "type": segment.type,
                "color": segment.color,
                "description": segment.description,
            },
            "member_count": 0,
            "repeat_purchase_share": 0.0,
            "stats": None,
            "channels": [],
            "categories": [],
            "top_members": [],
        }

    features_rows = _features_for_members(db, organization_id, member_ids)

    stats_rows = []
    for metric in FEATURE_METRICS:
        values = [float(row[metric]) for row in features_rows if row[metric] is not None]
        if values:
            stats_rows.append(
                {
                    "metric": metric,
                    "avg": round(sum(values) / len(values), 2),
                    "median": round(float(pd.Series(values).median()), 2),
                }
            )

    channels = _top_channels(db, organization_id, member_ids)
    categories = _top_categories(db, organization_id, member_ids)
    repeat_share = (
        round(sum(1 for row in features_rows if (row.get("frequency") or 0) >= 2) / len(features_rows), 4)
        if features_rows
        else 0.0
    )

    top_members = _top_members(db, organization_id, member_ids)

    return {
        "segment": {
            "id": segment.id,
            "name": segment.name,
            "type": segment.type,
            "color": segment.color,
            "description": segment.description,
        },
        "member_count": len(member_ids),
        "repeat_purchase_share": repeat_share,
        "stats": stats_rows,
        "channels": channels,
        "categories": categories,
        "top_members": top_members,
    }


def _features_for_members(db: Session, organization_id: int, member_ids: list[int]) -> list[dict]:
    if not member_ids:
        return []
    latest = (
        select(CustomerFeature.customer_id, func.max(CustomerFeature.id).label("latest_id"))
        .where(
            CustomerFeature.organization_id == organization_id,
            CustomerFeature.customer_id.in_(member_ids),
        )
        .group_by(CustomerFeature.customer_id)
        .subquery()
    )
    rows = db.execute(
        select(CustomerFeature).join(latest, latest.c.latest_id == CustomerFeature.id)
    ).scalars().all()
    return [
        {
            "customer_id": row.customer_id,
            "recency_days": row.recency_days,
            "frequency": row.frequency,
            "monetary": row.monetary,
            "aov": row.aov,
            "avg_days_between": row.avg_days_between,
            "tenure_days": row.tenure_days,
            "category_diversity": row.category_diversity,
            "discount_usage": row.discount_usage,
            "engagement_score": row.engagement_score,
            "clv_est": row.clv_est,
        }
        for row in rows
    ]


def _top_channels(db: Session, organization_id: int, member_ids: list[int]) -> list[dict]:
    if not member_ids:
        return []
    rows = db.execute(
        select(Transaction.channel, func.count(Transaction.id))
        .where(
            Transaction.organization_id == organization_id,
            Transaction.customer_id.in_(member_ids),
            Transaction.channel.isnot(None),
        )
        .group_by(Transaction.channel)
        .order_by(func.count(Transaction.id).desc())
        .limit(5)
    ).all()
    return [{"channel": channel, "orders": int(count)} for channel, count in rows]


def _top_categories(db: Session, organization_id: int, member_ids: list[int]) -> list[dict]:
    if not member_ids:
        return []
    rows = db.execute(
        select(Product.category, func.coalesce(func.sum(Transaction.amount), 0))
        .select_from(Transaction)
        .join(Product, Product.id == Transaction.product_id)
        .where(
            Transaction.organization_id == organization_id,
            Transaction.customer_id.in_(member_ids),
            Product.category.isnot(None),
        )
        .group_by(Product.category)
        .order_by(func.coalesce(func.sum(Transaction.amount), 0).desc())
        .limit(5)
    ).all()
    return [{"category": category, "revenue": round(float(revenue), 2)} for category, revenue in rows]


def _top_members(db: Session, organization_id: int, member_ids: list[int]) -> list[dict]:
    if not member_ids:
        return []
    features = _features_for_members(db, organization_id, member_ids)
    customers = {
        row.id: row
        for row in db.execute(
            select(Customer).where(Customer.organization_id == organization_id, Customer.id.in_(member_ids))
        ).scalars().all()
    }
    ranked = sorted(
        (row for row in features if row.get("clv_est") is not None),
        key=lambda row: row["clv_est"],
        reverse=True,
    )
    out = []
    for row in ranked[:10]:
        cust = customers.get(row["customer_id"])
        out.append(
            {
                "customer_id": row["customer_id"],
                "name": cust.name if cust else None,
                "external_customer_id": cust.external_customer_id if cust else None,
                "clv_est": row["clv_est"],
                "engagement_score": row["engagement_score"],
                "frequency": row["frequency"],
            }
        )
    return out