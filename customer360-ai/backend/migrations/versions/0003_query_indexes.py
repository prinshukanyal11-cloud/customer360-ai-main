"""query indexes (Phase 12)

Adds three composite indexes for the read paths that every dashboard load, drift
scan, and predictions export depends on. Each was chosen from the predicates the
service layer actually issues, not from the column list:

- predictions (organization_id, model_version_id, customer_id)
  Every read of `predictions` is scoped to one model version: the dashboard risk
  counts (dashboard_service), the drift histogram and prediction baseline
  (monitoring_service), and the predictions report (reports_service), which also
  orders by (model_version_id, customer_id). Single-column indexes cannot serve
  that pair; this ordering also satisfies the export's sort from the index.

- model_versions (organization_id, active)
  "Which version is active?" runs before scoring, drift, the dashboard, and
  campaign audience resolution.

- customer_features (organization_id, dataset_version_id)
  Drift monitoring compares the current feature rows against the rows of one
  training dataset version. The existing uniqueness constraint leads with
  customer_id, so it cannot serve this lookup - without the index the comparison
  reads every feature row the tenant owns.

Index maintenance is not free: `score_customers` deletes and reinserts all
prediction rows per version, so this is deliberately limited to the three
measured hot paths rather than every filtered column.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-03
"""
from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

INDEXES = (
    ("ix_predictions_org_version_customer", "predictions", ["organization_id", "model_version_id", "customer_id"]),
    ("ix_model_versions_org_active", "model_versions", ["organization_id", "active"]),
    ("ix_cfeatures_org_version", "customer_features", ["organization_id", "dataset_version_id"]),
)


def upgrade() -> None:
    for name, table, columns in INDEXES:
        op.create_index(name, table, columns)


def downgrade() -> None:
    for name, table, _columns in reversed(INDEXES):
        op.drop_index(name, table_name=table)
