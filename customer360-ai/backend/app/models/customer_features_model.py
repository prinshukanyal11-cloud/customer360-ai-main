"""Customer features, segmentation, models, and predictions."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixin import OrgScopedMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.transaction import Customer


class CustomerFeature(OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "customer_features"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "customer_id", "dataset_version_id", name="uq_cfeatures_cust_ver"
        ),
        # The uniqueness constraint leads with customer_id, so it cannot serve a
        # lookup by dataset version. Drift monitoring compares the current feature
        # rows against one training dataset version, which without this walks every
        # feature row the tenant owns.
        Index("ix_cfeatures_org_version", "organization_id", "dataset_version_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True)
    dataset_version_id: Mapped[int] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # RFM
    recency_days: Mapped[float | None] = mapped_column(Float, nullable=True)
    frequency: Mapped[float | None] = mapped_column(Float, nullable=True)
    monetary: Mapped[float | None] = mapped_column(Float, nullable=True)
    rfm_score: Mapped[str | None] = mapped_column(String(8), nullable=True)
    rfm_segment: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Behavioral
    aov: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_days_between: Mapped[float | None] = mapped_column(Float, nullable=True)
    tenure_days: Mapped[float | None] = mapped_column(Float, nullable=True)
    category_diversity: Mapped[int | None] = mapped_column(nullable=True)
    discount_usage: Mapped[float | None] = mapped_column(Float, nullable=True)
    engagement_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    clv_est: Mapped[float | None] = mapped_column(Float, nullable=True)

    customer: Mapped["Customer"] = relationship(back_populates="features")


class Segment(OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "segments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(16), nullable=False)  # RFM | ML
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    rules_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    color: Mapped[str | None] = mapped_column(String(32), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, server_default="true", nullable=False)


class CustomerSegment(OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "customer_segments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    segment_id: Mapped[int] = mapped_column(
        ForeignKey("segments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    customer: Mapped["Customer"] = relationship(back_populates="segments")


class Model(OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "models"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    model_type: Mapped[str] = mapped_column(String(32), nullable=False)  # churn | segmentation | clv | next_purchase
    family: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), server_default="TRAINED", nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    versions: Mapped[list["ModelVersion"]] = relationship(back_populates="model", cascade="all, delete-orphan")


class ModelVersion(OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "model_versions"
    # Resolving "the active version" is the gateway to nearly every ML operation
    # (dashboard KPIs, scoring, drift, reports), and it always filters on the pair.
    __table_args__ = (Index("ix_model_versions_org_active", "organization_id", "active"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("models.id", ondelete="CASCADE"), nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(nullable=False)
    artifact_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    preprocessing_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    features_list: Mapped[list | None] = mapped_column(JSON, nullable=True)
    params_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    metrics_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    train_dataset_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="SET NULL"), nullable=True
    )
    random_seed: Mapped[int | None] = mapped_column(nullable=True)
    training_duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    trained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    model: Mapped[Model] = relationship(back_populates="versions")


class Prediction(OrgScopedMixin, Base):
    __tablename__ = "predictions"
    # Every read of this table is scoped to one model version - the dashboard risk
    # counts, the monitoring histogram, the drift baseline, and the predictions
    # export all filter on (organization_id, model_version_id). Individual indexes
    # on each column cannot serve that pair, and the export additionally sorts by
    # (model_version_id, customer_id), which this ordering satisfies from the index.
    __table_args__ = (
        Index("ix_predictions_org_version_customer", "organization_id", "model_version_id", "customer_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    model_version_id: Mapped[int] = mapped_column(
        ForeignKey("model_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    prediction_label: Mapped[bool | None] = mapped_column(nullable=True)
    prediction_proba: Mapped[float] = mapped_column(Float, nullable=False)
    risk_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    score_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    inferred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    customer: Mapped["Customer"] = relationship(back_populates="predictions")


class Explanation(OrgScopedMixin, Base):
    __tablename__ = "explanations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    prediction_id: Mapped[int] = mapped_column(
        ForeignKey("predictions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    method: Mapped[str] = mapped_column(String(32), server_default="SHAP", nullable=False)
    base_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    global_importance_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    local_contrib_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RetentionRecommendation(OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)  # RETENTION | PRODUCT
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    priority_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_objective: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rules_triggered_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    model_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_versions.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)