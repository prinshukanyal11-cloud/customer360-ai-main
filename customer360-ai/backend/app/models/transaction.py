"""Customer, Transaction, and Product models."""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixin import OrgScopedMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.customer_feature import CustomerFeature
    from app.models.customer_segment import CustomerSegment
    from app.models.prediction import Prediction


class Customer(OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("organization_id", "external_customer_id", name="uq_customers_org_extid"),
        Index("ix_customers_org_signup", "organization_id", "signup_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    external_customer_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    age: Mapped[int | None] = mapped_column(nullable=True)
    gender: Mapped[str | None] = mapped_column(String(32), nullable=True)
    signup_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    acquisition_channel: Mapped[str | None] = mapped_column(String(64), nullable=True)

    features: Mapped[list["CustomerFeature"]] = relationship(back_populates="customer", cascade="all, delete-orphan")
    segments: Mapped[list["CustomerSegment"]] = relationship(back_populates="customer", cascade="all, delete-orphan")
    predictions: Mapped[list["Prediction"]] = relationship(back_populates="customer", cascade="all, delete-orphan")


class Product(OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("organization_id", "external_product_id", name="uq_products_org_extid"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    external_product_id: Mapped[str] = mapped_column(String(255), nullable=False)
    product_name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    subcategory: Mapped[str | None] = mapped_column(String(128), nullable=True)
    unit_price: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)


class Transaction(OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_transactions_org_date", "organization_id", "order_date"),
        Index("ix_transactions_org_customer", "organization_id", "customer_id"),
        UniqueConstraint("organization_id", "external_order_id", name="uq_transactions_org_order"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True)
    external_order_id: Mapped[str] = mapped_column(String(255), nullable=False)
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    quantity: Mapped[int | None] = mapped_column(nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    discount: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    channel: Mapped[str | None] = mapped_column(String(64), nullable=True)