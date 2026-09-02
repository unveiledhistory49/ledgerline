"""SQLAlchemy domain models. The ledger is append-only: posted rows are never mutated."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.utcnow()


class Base(DeclarativeBase):
    pass


class AccountType(enum.StrEnum):
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    REVENUE = "revenue"
    EXPENSE = "expense"


DEBIT_NORMAL = {AccountType.ASSET, AccountType.EXPENSE}
CREDIT_NORMAL = {AccountType.LIABILITY, AccountType.EQUITY, AccountType.REVENUE}


class JournalStatus(enum.StrEnum):
    PENDING = "pending"
    POSTED = "posted"
    VOIDED = "voided"
    REVERSED = "reversed"


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    api_key_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)

    accounts: Mapped[list[Account]] = relationship(
        back_populates="org", cascade="all, delete-orphan"
    )


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("org_id", "code", name="uq_accounts_org_code"),
        Index("ix_accounts_org_type", "org_id", "type"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[AccountType] = mapped_column(String(16), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    is_locked: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)

    org: Mapped[Organization] = relationship(back_populates="accounts")


class Journal(Base):
    __tablename__ = "journals"
    __table_args__ = (Index("ix_journals_org_posted", "org_id", "posted_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    reference: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=JournalStatus.POSTED.value
    )
    reversal_of_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("journals.id"), nullable=True, default=None
    )
    reversed_by_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("journals.id"), nullable=True, default=None
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    posted_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)

    entries: Mapped[list[Entry]] = relationship(
        back_populates="journal", cascade="all, delete-orphan"
    )


class Entry(Base):
    __tablename__ = "entries"
    __table_args__ = (
        CheckConstraint(
            "((debit_minor > 0 AND credit_minor = 0) OR (credit_minor > 0 AND debit_minor = 0))",
            name="ck_entries_one_side",
        ),
        CheckConstraint("debit_minor >= 0 AND credit_minor >= 0", name="ck_entries_nonneg"),
        Index("ix_entries_account", "account_id"),
        Index("ix_entries_journal", "journal_id"),
        Index("ix_entries_org", "org_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    journal_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("journals.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    debit_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    credit_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    memo: Mapped[str] = mapped_column(String(300), nullable=False, default="")

    journal: Mapped[Journal] = relationship(back_populates="entries")


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("org_id", "key", name="uq_idem_org_key"),)
    org_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    path: Mapped[str] = mapped_column(String(300), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    journal_id: Mapped[str | None] = mapped_column(String(36), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class FxRate(Base):
    __tablename__ = "fx_rates"
    __table_args__ = (Index("ix_fx_pair_time", "base", "quote", "effective_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    base: Mapped[str] = mapped_column(String(3), nullable=False)
    quote: Mapped[str] = mapped_column(String(3), nullable=False)
    rate: Mapped[str] = mapped_column(String(40), nullable=False)  # Decimal as string, exact
    effective_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class PeriodClose(Base):
    __tablename__ = "period_closes"
    __table_args__ = (UniqueConstraint("org_id", "period", name="uq_close_org_period"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    period: Mapped[str] = mapped_column(String(7), nullable=False)  # YYYY-MM
    trial_balance_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    closed_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class WebhookEndpoint(Base):
    __tablename__ = "webhook_endpoints"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    secret: Mapped[str] = mapped_column(String(128), nullable=False)
    active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (Index("ix_delivery_status_retry", "status", "next_retry_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    endpoint_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), nullable=False
    )
    org_id: Mapped[str] = mapped_column(String(36), nullable=False)
    event: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_retry_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
