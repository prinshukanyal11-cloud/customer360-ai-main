"""MonitoringRun: persisted drift/health snapshots for a trained model version.

Feature drift is measured against the feature rows of the dataset version the
model was trained on (the true train-time baseline). Prediction drift is
measured against the earliest recorded run for the same version, because
`score_customers` replaces prediction rows in place and keeps no history.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixin import OrgScopedMixin, TimestampMixin


class MonitoringRun(OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "monitoring_runs"
    # Declared here as well as in migration 0002: without it in the metadata,
    # `alembic check` reads the index as drift and asks to drop an index that
    # the run-history and prediction-baseline queries depend on. It serves
    # "newest run for a version" (baseline lookup) and "history for a version".
    __table_args__ = (
        Index("ix_monitoring_runs_org_version_created", "organization_id", "model_version_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("models.id", ondelete="CASCADE"), nullable=False, index=True)
    model_version_id: Mapped[int] = mapped_column(
        ForeignKey("model_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), server_default="COMPLETED", nullable=False)
    # Worst per-feature verdict across the profiled features.
    drift_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    psi_overall: Mapped[float | None] = mapped_column(Float, nullable=True)
    n_features_drifted: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    drifted_features_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    feature_metrics_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    prediction_psi: Mapped[float | None] = mapped_column(Float, nullable=True)
    prediction_drift_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    prediction_summary_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    health_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    n_reference_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_current_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reference_dataset_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="SET NULL"), nullable=True
    )
    baseline_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("monitoring_runs.id", ondelete="SET NULL"), nullable=True
    )
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    triggered_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def metrics(self) -> dict:
        """Compact view used by the runs history endpoint."""
        return {
            "status": self.status,
            "drift_status": self.drift_status,
            "psi_overall": self.psi_overall,
            "n_features_drifted": self.n_features_drifted,
            "drifted_features": self.drifted_features_json or [],
            "prediction_psi": self.prediction_psi,
            "prediction_drift_status": self.prediction_drift_status,
            "n_reference_rows": self.n_reference_rows,
            "n_current_rows": self.n_current_rows,
            "duration_s": self.duration_s,
            "error": self.error,
        }
