"""Customer endpoints: server-side paginated list + Customer 360 detail."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.core.constants import Roles
from app.core.exceptions import NotFoundError
from app.core.security_deps import CurrentUser, DbSession, require_roles
from app.ml.explain import explanation_for_customer
from app.models.user import User
from app.services.customer_service import get_customer_360, list_customers
from app.services.whatif_service import simulate_whatif

router = APIRouter(prefix="/customers", tags=["customers"])

MutableRoles = Annotated[User, Depends(require_roles(Roles.ADMIN, Roles.ANALYST))]


class WhatIfRequest(BaseModel):
    scenario: str | None = Field(default=None)
    changes: dict[str, float] | None = Field(default=None)


@router.get("")
def list_customers_endpoint(
    db: DbSession,
    user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    search: str | None = Query(None),
) -> dict:
    return list_customers(db, user.organization_id, page=page, page_size=page_size, search=search)


@router.get("/{customer_id}")
def customer_360(db: DbSession, user: CurrentUser, customer_id: int) -> dict:
    result = get_customer_360(db, user.organization_id, customer_id)
    if result is None:
        raise NotFoundError("Customer not found in this organization.")
    return result


@router.get("/{customer_id}/explanation")
def customer_explanation(db: DbSession, user: CurrentUser, customer_id: int) -> dict:
    if get_customer_360(db, user.organization_id, customer_id) is None:
        raise NotFoundError("Customer not found in this organization.")
    return explanation_for_customer(db, user.organization_id, customer_id)


@router.post("/{customer_id}/what-if")
def customer_whatif(db: DbSession, user: MutableRoles, customer_id: int, body: WhatIfRequest) -> dict:
    return simulate_whatif(
        db,
        user.organization_id,
        customer_id,
        changes=body.changes,
        scenario=body.scenario,
    )