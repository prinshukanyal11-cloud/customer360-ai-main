"""Dataset and DatasetVersion models."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixin import OrgScopedMixin, TimestampMixin

if TYPE_CHECKING:  # pragma: no cover
    pass


class Dataset(OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "datasets"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_datasets_org_name"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(String(32), server_default="csv", nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), server_default="UPLOADED", nullable=False
    )  # UPLOADED|MAPPED|VALIDATED|PROCESSED|FAILED
    total_rows: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    columns_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    versions: Mapped[list["DatasetVersion"]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan"
    )


class DatasetVersion(OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "dataset_versions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    row_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    column_mapping_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    dq_score: Mapped[float | None] = mapped_column(nullable=True)
    dq_report_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    dataset: Mapped[Dataset] = relationship(back_populates="versions")