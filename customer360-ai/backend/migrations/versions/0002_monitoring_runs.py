"""monitoring runs (Phase 11)

Adds the persisted drift/health snapshot table used by model monitoring. This is
the first hand-written (non-autogenerate) revision; `0001` was a raw SQL dump.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-03
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "monitoring_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.Integer(), nullable=False),
        sa.Column("model_version_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="COMPLETED", nullable=False),
        sa.Column("drift_status", sa.String(length=16), nullable=True),
        sa.Column("psi_overall", sa.Float(), nullable=True),
        sa.Column("n_features_drifted", sa.Integer(), server_default="0", nullable=False),
        sa.Column("drifted_features_json", sa.JSON(), nullable=True),
        sa.Column("feature_metrics_json", sa.JSON(), nullable=True),
        sa.Column("prediction_psi", sa.Float(), nullable=True),
        sa.Column("prediction_drift_status", sa.String(length=16), nullable=True),
        sa.Column("prediction_summary_json", sa.JSON(), nullable=True),
        sa.Column("health_json", sa.JSON(), nullable=True),
        sa.Column("n_reference_rows", sa.Integer(), nullable=True),
        sa.Column("n_current_rows", sa.Integer(), nullable=True),
        sa.Column("reference_dataset_version_id", sa.Integer(), nullable=True),
        sa.Column("baseline_run_id", sa.Integer(), nullable=True),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("triggered_by", sa.Integer(), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["model_version_id"], ["model_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["reference_dataset_version_id"], ["dataset_versions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["baseline_run_id"], ["monitoring_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["triggered_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_monitoring_runs_organization_id", "monitoring_runs", ["organization_id"])
    op.create_index("ix_monitoring_runs_model_id", "monitoring_runs", ["model_id"])
    op.create_index("ix_monitoring_runs_model_version_id", "monitoring_runs", ["model_version_id"])
    op.create_index(
        "ix_monitoring_runs_org_version_created",
        "monitoring_runs",
        ["organization_id", "model_version_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_monitoring_runs_org_version_created", table_name="monitoring_runs")
    op.drop_index("ix_monitoring_runs_model_version_id", table_name="monitoring_runs")
    op.drop_index("ix_monitoring_runs_model_id", table_name="monitoring_runs")
    op.drop_index("ix_monitoring_runs_organization_id", table_name="monitoring_runs")
    op.drop_table("monitoring_runs")
