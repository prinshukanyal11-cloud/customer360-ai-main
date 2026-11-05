"""Pydantic v2 schemas — a representative subset for the skeleton.

These will grow domain-by-domain as phases are implemented.
"""
from __future__ import annotations

from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Auth -------------------------------------------------------------------
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = None
    organization_name: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class OrganizationOut(ORMModel):
    id: int
    name: str
    slug: str


class UserOut(ORMModel):
    id: int
    email: str
    full_name: str | None
    role: str
    is_active: bool


# --- Meta -------------------------------------------------------------------
class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int


class ApiEnvelope(BaseModel, Generic[T]):
    data: T
    meta: dict | None = None


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict | None = None


# --- Health -----------------------------------------------------------------
class HealthCheck(BaseModel):
    status: str
    components: dict[str, str]
    version: str
    timestamp: datetime


# --- Dataset -----------------------------------------------------------------
class DatasetOut(ORMModel):
    id: int
    organization_id: int
    name: str
    description: str | None
    source_type: str
    status: str
    total_rows: int | None
    created_at: datetime