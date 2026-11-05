"""SQLAlchemy model registry (import all models so metadata is complete)."""
from __future__ import annotations

from app.models.auth_tokens import LoginFailure, RefreshToken
from app.models.campaign import AuditLog, Campaign, CampaignMember, CampaignResult
from app.models.customer_features_model import (
    CustomerFeature,
    CustomerSegment,
    Explanation,
    Model,
    ModelVersion,
    Prediction,
    RetentionRecommendation,
    Segment,
)
from app.models.dataset import Dataset, DatasetVersion
from app.models.mixin import OrgScopedMixin, TimestampMixin
from app.models.monitoring import MonitoringRun
from app.models.transaction import Customer, Product, Transaction
from app.models.user import Organization, User

__all__ = [
    "AuditLog",
    "Campaign",
    "CampaignMember",
    "CampaignResult",
    "Customer",
    "CustomerFeature",
    "CustomerSegment",
    "Dataset",
    "DatasetVersion",
    "Explanation",
    "LoginFailure",
    "Model",
    "ModelVersion",
    "MonitoringRun",
    "Organization",
    "Prediction",
    "Product",
    "RetentionRecommendation",
    "RefreshToken",
    "Segment",
    "Transaction",
    "User",
    "OrgScopedMixin",
    "TimestampMixin",
]