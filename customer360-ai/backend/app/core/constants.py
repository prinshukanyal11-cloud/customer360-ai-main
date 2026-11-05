"""Domain constants that can be configured by administrators later."""
from __future__ import annotations


class Roles:
    ADMIN = "ADMIN"
    ANALYST = "ANALYST"
    VIEWER = "VIEWER"

    ALL = (ADMIN, ANALYST, VIEWER)


class RiskLevel:
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    ALL = (LOW, MEDIUM, HIGH, CRITICAL)


class RiskCategory:
    """CLV x churn matrix quadrant: value at risk x churn probability."""

    PRIORITY = "PRIORITY"  # high risk + high value
    WIN_BACK = "WIN_BACK"  # high risk + low value
    GROW = "GROW"  # low risk + high value
    MONITOR = "MONITOR"  # low risk + low value

    ALL = (PRIORITY, WIN_BACK, GROW, MONITOR)


class RecommendationType:
    RETENTION = "RETENTION"
    PRODUCT = "PRODUCT"

    ALL = (RETENTION, PRODUCT)


class RecommendationAction:
    """Canonical retention actions used by the rule engine."""

    WIN_BACK_OFFER = "WIN_BACK_OFFER"
    LOYALTY_PROGRAM = "LOYALTY_PROGRAM"
    REENGAGEMENT = "REENGAGEMENT"
    CROSS_SELL = "CROSS_SELL"
    SUBSCRIPTION_PUSH = "SUBSCRIPTION_PUSH"
    CONCIERGE_SAVE = "CONCIERGE_SAVE"
    CYCLE_REDUCE = "CYCLE_REDUCE"
    NOURISH = "NOURISH"

    ALL = (
        WIN_BACK_OFFER,
        LOYALTY_PROGRAM,
        REENGAGEMENT,
        CROSS_SELL,
        SUBSCRIPTION_PUSH,
        CONCIERGE_SAVE,
        CYCLE_REDUCE,
        NOURISH,
    )


class RfmSegment:
    CHAMPIONS = "champions"
    LOYAL = "loyal_customers"
    POTENTIAL_LOYALISTS = "potential_loyalists"
    NEW_CUSTOMERS = "new_customers"
    PROMISING = "promising"
    NEED_ATTENTION = "need_attention"
    AT_RISK = "at_risk"
    CANT_LOSE = "cannot_lose_them"
    HIBERNATING = "hibernating"
    LOST = "lost"

    ALL = (
        CHAMPIONS,
        LOYAL,
        POTENTIAL_LOYALISTS,
        NEW_CUSTOMERS,
        PROMISING,
        NEED_ATTENTION,
        AT_RISK,
        CANT_LOSE,
        HIBERNATING,
        LOST,
    )


class PriorityLevel:
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

    ALL = (HIGH, MEDIUM, LOW)


class JourneyStage:
    """Lifecycle state of a customer based on purchase count + recency."""

    NEW = "NEW"  # single purchase, recent
    EARLY = "EARLY"  # 2-3 purchases, recent
    REGULAR = "REGULAR"  # 4+ purchases, recent
    AT_RISK = "AT_RISK"  # engaged but recency in middle band
    DORMANT = "DORMANT"  # long absence, borderline lost
    LOST = "LOST"  # very long absence

    ALL = (NEW, EARLY, REGULAR, AT_RISK, DORMANT, LOST)


class TaskStatus:
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

    ALL = (QUEUED, RUNNING, COMPLETED, FAILED)


class CampaignStatus:
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"

    ALL = (DRAFT, ACTIVE, PAUSED, COMPLETED)


class CampaignChannel:
    EMAIL = "email"
    SMS = "sms"
    PUSH = "push"
    IN_APP = "in_app"
    ALL = (EMAIL, SMS, PUSH, IN_APP)


class CampaignVariant:
    """A/B test arm. CONTROL receives nothing (or the status quo offer)."""

    CONTROL = "A"
    TREATMENT = "B"

    ALL = (CONTROL, TREATMENT)


class CampaignMemberStatus:
    ELIGIBLE = "ELIGIBLE"
    SENT = "SENT"
    RESPONDED = "RESPONDED"
    CONVERTED = "CONVERTED"
    FAILED = "FAILED"

    ALL = (ELIGIBLE, SENT, RESPONDED, CONVERTED, FAILED)


class DriftStatus:
    """Verdict for a single drift metric (PSI-based feature/prediction drift)."""

    STABLE = "STABLE"
    MODERATE = "MODERATE"
    SIGNIFICANT = "SIGNIFICANT"
    UNKNOWN = "UNKNOWN"
    NO_DATA = "NO_DATA"

    ALL = (STABLE, MODERATE, SIGNIFICANT, UNKNOWN, NO_DATA)


class DriftMetric:
    """What a drift measurement compares."""

    FEATURE = "FEATURE"
    PREDICTION = "PREDICTION"

    ALL = (FEATURE, PREDICTION)


class MonitoredModelType:
    """Model types with a stored feature contract that monitoring can profile."""

    CHURN = "churn"
    SEGMENTATION = "segmentation"

    ALL = (CHURN, SEGMENTATION)


class AuditAction:
    AUTH_LOGIN = "auth.login"
    AUTH_REGISTER = "auth.register"
    AUTH_LOGOUT = "auth.logout"
    DATASET_UPLOAD = "dataset.upload"
    DATASET_MAP = "dataset.map"
    DATASET_VALIDATE = "dataset.validate"
    DATASET_PROCESS = "dataset.process"
    FEATURES_BUILD = "features.build"
    MODEL_TRAIN = "model.train"
    MODEL_ACTIVATE = "model.activate"
    RECOMMENDATION_GENERATE = "recommendation.generate"
    WHATIF_SIMULATE = "whatif.simulate"
    AI_QUERY = "ai.query"
    AI_NL_TO_SQL = "ai.nl_to_sql"
    CAMPAIGN_CREATE = "campaign.create"
    CAMPAIGN_UPDATE = "campaign.update"
    CAMPAIGN_SIMULATE = "campaign.simulate"
    CAMPAIGN_START = "campaign.start"
    CAMPAIGN_RESULTS = "campaign.results"
    MONITORING_SCAN = "monitoring.scan"
    REPORT_EXPORT = "report.export"
    USER_MANAGE = "user.manage"