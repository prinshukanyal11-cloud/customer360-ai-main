"""Application settings loaded from environment variables (via pydantic-settings)."""
from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Values that ship in config.py and .env.example. A deployment that never
# overrides them is signing JWTs with a key that is published in the repository.
INSECURE_DEFAULTS = frozenset(
    {
        "change-me",
        "change-me-please-use-a-long-random-value",
        "change-me-please-use-a-different-long-random-value",
    }
)
# A short shared secret is brute-forceable offline even if it is not a default.
MIN_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        protected_namespaces=(),
    )

    app_name: str = "Customer360 AI"
    app_env: str = "development"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"

    # Security
    secret_key: str = "change-me"
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_minutes: int = 7 * 24 * 60

    # Sign-in lockout. Counts failures per submitted address, so it cannot be
    # used to discover which addresses are registered. Generous by design: a
    # lower threshold lets anyone who can reach the form lock a colleague out.
    login_lockout_enabled: bool = True
    login_lockout_threshold: int = 5
    login_lockout_minutes: int = 15
    # Failures older than this start a new count instead of accumulating.
    login_lockout_window_minutes: int = 15

    # Database
    database_url: str = "postgresql+psycopg://customer360:customer360@localhost:5432/customer360"
    database_url_ro: str = ""

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/0"

    # LLM provider abstraction
    llm_provider: str = "mock"  # openai | gemini | mock
    openai_api_key: str = ""
    gemini_api_key: str = ""
    default_llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.2
    llm_max_tokens: int = 1500
    llm_request_timeout_seconds: int = 60

    # Security / uploads
    cors_origins: str = "http://localhost:3000,http://localhost:5173"
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 120
    # Login is the endpoint worth throttling hardest: it is the only place where
    # a caller can guess a credential, so it gets its own tighter budget.
    auth_rate_limit_per_minute: int = 10
    # Behind nginx/ALB the socket peer is the proxy, so keying on request.client.host
    # would rate-limit every user together. Reading X-Forwarded-For fixes that but
    # lets a direct caller forge the header and sidestep the limit entirely, so it
    # is opt-in and only safe when the app is unreachable except through that proxy.
    rate_limit_trust_proxy: bool = False
    # Paths that stay reachable when the limiter is saturated, so a traffic spike
    # cannot make the orchestrator kill the container. Matched as path suffixes
    # (/health covers /api/v1/health without hardcoding the version prefix).
    rate_limit_exempt_paths: str = "/health,/ready,/docs,/openapi.json"
    # Where the fixed-window counters live. The in-process store gives every
    # replica its own budget, so N pods behind a load balancer allow N times the
    # configured rate; Redis makes the limit a property of the deployment.
    # "auto" uses Redis when it is reachable and degrades to memory with a logged
    # warning when it is not, which is what keeps a Redis outage from turning into
    # a total outage.
    rate_limit_backend: str = "auto"  # auto | memory | redis
    rate_limit_redis_url: str = ""
    rate_limit_redis_prefix: str = "ratelimit"
    security_headers_enabled: bool = True
    max_upload_mb: int = 50
    allowed_extensions: str = ".csv,.xlsx,.parquet"

    # Demo / sample data
    demo_mode: bool = False
    enable_sample_data: bool = False
    sample_data_path: str = "./data/sample"

    # ML artifacts
    model_artifact_dir: str = "./data/models/artifacts"

    # Observability
    log_level: str = "INFO"

    # Churn risk thresholds (configurable)
    churn_risk_thresholds: dict[str, float] = {"LOW": 0.35, "MEDIUM": 0.60, "HIGH": 0.80, "CRITICAL": 1.0}
    # A buyer is labelled churned when recency (days since last order, as of the
    # dataset's latest order date) exceeds this window.
    churn_window_days: int = 90

    # Drift monitoring (PSI thresholds follow the standard interpretation scale)
    drift_psi_thresholds: dict[str, float] = {"MODERATE": 0.10, "SIGNIFICANT": 0.25}
    # Equal-width bins drawn from the reference distribution quantiles.
    drift_bins: int = 10
    # Below this many scored/observed rows a metric is reported UNKNOWN rather
    # than as a confident verdict.
    monitoring_min_samples: int = 30
    # PSI over-fits small samples, so the bin count is reduced to at least this
    # many observations per bin (e.g. n=120 with 20/bin uses 6 bins, not 10).
    monitoring_min_per_bin: int = 20
    # Nudge towards retraining when the active version is older than this.
    monitoring_max_staleness_days: int = 90
    # Default page size for the monitoring run history.
    monitoring_history_limit: int = 50

    # Report exports
    report_max_rows: int = 50_000
    report_formats: str = "csv,xlsx,json"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def allowed_extension_list(self) -> list[str]:
        return [ext.strip().lower() for ext in self.allowed_extensions.split(",") if ext.strip()]

    @property
    def rate_limit_exempt_path_list(self) -> list[str]:
        return [path.strip() for path in self.rate_limit_exempt_paths.split(",") if path.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() in {"production", "prod"}

    @model_validator(mode="after")
    def _reject_insecure_production_config(self) -> Settings:
        """Fail closed on a production boot that would be trivially attackable.

        Development keeps the shipped defaults on purpose - a fresh clone must
        run without ceremony. Production must not, because the difference between
        the two is an authentication bypass (a forgeable token key) rather than a
        rough edge. Refusing to start is louder and safer than warning.
        """
        if not self.is_production:
            return self

        problems: list[str] = []
        for name in ("jwt_secret", "secret_key"):
            value = getattr(self, name)
            if value in INSECURE_DEFAULTS:
                problems.append(f"{name.upper()} is still the shipped placeholder")
            elif len(value) < MIN_SECRET_LENGTH:
                problems.append(f"{name.upper()} is shorter than {MIN_SECRET_LENGTH} characters")
        if self.debug:
            problems.append("DEBUG is true (exposes stack traces and disables production error handling)")
        if "*" in self.cors_origin_list:
            problems.append("CORS_ORIGINS contains '*', which with credentials lets any origin call the API")
        if not self.database_url_ro:
            problems.append("DATABASE_URL_RO is empty, so NL-to-SQL would fall back to a read-write connection")

        if problems:
            raise ValueError(
                "Refusing to start with an insecure production configuration: "
                + "; ".join(problems)
                + ". Set the values in the environment (see .env.example) before deploying."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


def get_redis_client():
    """A Redis client for rate-limit counters.

    Separate from the Celery client on purpose: the limiter must never be able to
    take the worker down, and it wants short socket timeouts so an unreachable
    Redis degrades the counter instead of holding a request open.
    """
    import redis

    url = settings.rate_limit_redis_url or settings.redis_url
    return redis.Redis.from_url(
        url,
        socket_connect_timeout=1,
        socket_timeout=1,
        health_check_interval=30,
    )