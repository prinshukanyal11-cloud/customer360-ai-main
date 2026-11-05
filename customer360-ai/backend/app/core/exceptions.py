"""Application exceptions mapped to friendly HTTP responses.

Technical details are logged server-side; users receive friendly, safe messages.
"""
from __future__ import annotations


class AppError(Exception):
    """Base application error with a user-facing message."""

    status_code = 500
    code = "INTERNAL_ERROR"
    message = "Something went wrong. Please try again later."

    def __init__(self, message: str | None = None, *, details: dict | None = None) -> None:
        self.message = message or self.message
        self.details = details or {}
        super().__init__(self.message)


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"


class ValidationError(AppError):
    status_code = 422
    code = "VALIDATION_ERROR"


class ForbiddenError(AppError):
    status_code = 403
    code = "FORBIDDEN"


class UnauthorizedError(AppError):
    status_code = 401
    code = "UNAUTHORIZED"


class InsufficientDataError(AppError):
    status_code = 422
    code = "INSUFFICIENT_DATA"
    message = "I don't have enough data to answer this request."


class DatasetColumnMappingError(ValidationError):
    code = "COLUMN_MAPPING_ERROR"
    message = "Customer ID column could not be identified. Please map your dataset columns before continuing."