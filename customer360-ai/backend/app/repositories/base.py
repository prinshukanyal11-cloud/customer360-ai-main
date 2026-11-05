"""Generic repository base with org-scoping helpers."""
from __future__ import annotations

from typing import Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    model: type[ModelT]

    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, entity_id: int) -> ModelT | None:
        return self.db.get(self.model, entity_id)

    def get_by_id_in_org(self, org_id: int, entity_id: int) -> ModelT | None:
        stmt = select(self.model).where(self.model.id == entity_id, self.model.organization_id == org_id)
        return self.db.execute(stmt).scalar_one_or_none()

    def list_all(self, *, limit: int = 100, offset: int = 0) -> list[ModelT]:
        stmt = select(self.model).limit(limit).offset(offset)
        return list(self.db.execute(stmt).scalars().all())

    def list_in_org(self, org_id: int, *, limit: int = 100, offset: int = 0) -> list[ModelT]:
        stmt = (
            select(self.model)
            .where(self.model.organization_id == org_id)
            .order_by(self.model.id)
            .limit(limit)
            .offset(offset)
        )
        return list(self.db.execute(stmt).scalars().all())

    def add(self, entity: ModelT) -> ModelT:
        self.db.add(entity)
        return entity

    def flush(self) -> None:
        self.db.flush()