"""Structured JSON logging with structlog.

- Logs authentication events, dataset uploads, model training, API errors,
  campaign creation, AI requests, and admin actions (callers add the context).
- Sensitive values (passwords, API keys, PII) must never be logged.
"""
from __future__ import annotations

import logging
import sys

import structlog

from app.core.config import settings


def setup_logging() -> None:
    logging.basicConfig(stream=sys.stdout, level=settings.log_level.upper(), format="%(message)s")

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


get_logger = structlog.get_logger