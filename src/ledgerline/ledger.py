"""Core double-entry posting logic. All invariants enforced here, not in the API layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ledgerline.models import (
    Account,
    Entry,
    Journal,
    JournalStatus,
    PeriodClose,
)
from ledgerline.money import MAX_MINOR, normalize_currency


class LedgerError(Exception):
    pass


class UnbalancedJournalError(LedgerError):
    pass


class PeriodClosedError(LedgerError):
    pass


class AccountLockedError(LedgerError):
    pass


class InsufficientFundsError(LedgerError):
    pass


class AlreadyReversedError(LedgerError):
    pass


@dataclass(frozen=True)
class JournalLine:
    account_id: str
    debit_minor: int = 0
    credit_minor: int = 0
    memo: str = ""


def _period_of(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


def is_period_closed(db: Session, org_id: str, dt: datetime) -> bool:
    period = _period_of(dt)
    row = db.execute(
        select(PeriodClose).where(PeriodClose.org_id == org_id, PeriodClose.period == period)
    ).scalar_one_or_none()
    return row is not None


def validate_lines(lines: list[JournalLine]) -> tuple[int, int]:
    if len(lines) < 2:
        raise UnbalancedJournalError("journal requires at least 2 entry lines")
    total_d = 0
    total_c = 0
    for ln in lines:
        d, c = ln.debit_minor, ln.credit_minor
        if not isinstance(d, int) or not isinstance(c, int):
            raise UnbalancedJournalError("amounts must be integers in minor units")
        if d < 0 or c < 0:
            raise UnbalancedJournalError("amounts must be non-negative")
        if d > MAX_MINOR or c > MAX_MINOR:
            raise UnbalancedJournalError("line amount exceeds maximum ledger value")
        one_sided = (d > 0) != (c > 0)
        if not one_sided:
            raise UnbalancedJournalError(
                "each line must be exactly one-sided (debit XOR credit), nonzero"
            )
        total_d += d
        total_c += c
        if total_d > MAX_MINOR * 100 or total_c > MAX_MINOR * 100:
            raise UnbalancedJournalError("journal total overflows ledger limits")
    if total_d <= 0 or total_d != total_c:
        raise UnbalancedJournalError(f"journal must balance: debits={total_d} credits={total_c}")
    return total_d, total_c


def load_journal_accounts(
    db: Session, *, org_id: str, account_ids: list[str], currency: str
) -> dict[str, Account]:
    """Fetch accounts and enforce org ownership, lock state, and currency match."""
    accounts = db.execute(select(Account).where(Account.id.in_(account_ids))).scalars().all()
    by_id = {a.id: a for a in accounts}
    if len(by_id) != len(set(account_ids)):
        missing = sorted(set(account_ids) - set(by_id))
        raise LedgerError(f"unknown accounts: {missing}")
    for a in by_id.values():
        if a.org_id != org_id:
            raise LedgerError(f"account {a.id} does not belong to org")
        if a.is_locked:
            raise AccountLockedError(f"account {a.code} is locked")
        if a.currency != currency:
            raise LedgerError(
                f"account {a.code} is {a.currency}, journal is {currency} "
                "(no mixed-currency journals)"
            )
    return by_id


def post_journal(
    db: Session,
    *,
    org_id: str,
    lines: list[JournalLine],
    currency: str,
    description: str = "",
    reference: str = "",
    posted_at: datetime | None = None,
    idempotency_key: str | None = None,
    allow_overdraft: bool = True,
) -> Journal:
    """Post a balanced journal atomically. Raises LedgerError subclasses on violation."""
    cur = normalize_currency(currency)
    validate_lines(lines)
    ts = posted_at or datetime.utcnow()
    if is_period_closed(db, org_id, ts):
        raise PeriodClosedError(f"period {_period_of(ts)} is closed for org {org_id}")

    account_ids = [ln.account_id for ln in lines]
    by_id = load_journal_accounts(db, org_id=org_id, account_ids=account_ids, currency=cur)

    journal = Journal(
        org_id=org_id,
        description=description[:500],
        reference=reference[:120],
        currency=cur,
        status=JournalStatus.POSTED.value,
        idempotency_key=idempotency_key,
        posted_at=ts,
    )
    db.add(journal)
    db.flush()  # assign id
    for ln in lines:
        db.add(
            Entry(
                org_id=org_id,
                journal_id=journal.id,
                account_id=ln.account_id,
                debit_minor=ln.debit_minor,
                credit_minor=ln.credit_minor,
                memo=ln.memo[:300],
            )
        )
    for a in by_id.values():
        a.version = a.version + 1
    db.flush()
    # Defensive re-read: confirm balance from rows actually written.
    totals = db.execute(
        select(func.sum(Entry.debit_minor), func.sum(Entry.credit_minor)).where(
            Entry.journal_id == journal.id
        )
    ).one()
    if totals[0] != totals[1] or not totals[0]:
        raise UnbalancedJournalError("post-write balance check failed")
    # Reserved: per-account overdraft policy hooks in future; permissive for now.
    _ = allow_overdraft
    return journal


def reverse_journal(
    db: Session,
    *,
    org_id: str,
    journal_id: str,
    description: str = "",
    posted_at: datetime | None = None,
) -> Journal:
    """Create a compensating journal that mirrors the original (append-only reversal)."""
    orig = db.execute(
        select(Journal).where(Journal.id == journal_id, Journal.org_id == org_id)
    ).scalar_one_or_none()
    if orig is None:
        raise LedgerError(f"journal {journal_id} not found")
    if orig.reversed_by_id:
        raise AlreadyReversedError(
            f"journal {journal_id} already reversed by {orig.reversed_by_id}"
        )
    if orig.status != JournalStatus.POSTED.value:
        raise LedgerError(f"only POSTED journals can be reversed (status={orig.status})")
    ts = posted_at or datetime.utcnow()
    if is_period_closed(db, org_id, ts):
        raise PeriodClosedError(f"period {_period_of(ts)} is closed")
    if is_period_closed(db, org_id, orig.posted_at):
        raise PeriodClosedError("cannot reverse a journal in a closed period")

    entries = db.execute(select(Entry).where(Entry.journal_id == orig.id)).scalars().all()
    if not entries:
        raise LedgerError("original journal has no entries")
    mirror = [
        JournalLine(
            account_id=e.account_id,
            debit_minor=e.credit_minor,
            credit_minor=e.debit_minor,
            memo=f"reversal of {e.id}",
        )
        for e in entries
    ]
    rev = post_journal(
        db,
        org_id=org_id,
        lines=mirror,
        currency=orig.currency,
        description=description or f"Reversal of {orig.id}",
        reference=f"REV:{orig.reference}"[:120],
        posted_at=ts,
    )
    rev.reversal_of_id = orig.id
    orig.reversed_by_id = rev.id
    orig.status = JournalStatus.REVERSED.value
    db.flush()
    return rev


def account_balances(db: Session, *, org_id: str, account_id: str) -> dict[str, int]:
    row = db.execute(
        select(
            func.coalesce(func.sum(Entry.debit_minor), 0),
            func.coalesce(func.sum(Entry.credit_minor), 0),
        ).where(Entry.org_id == org_id, Entry.account_id == account_id)
    ).one()
    return {
        "debit_turnover": int(row[0]),
        "credit_turnover": int(row[1]),
        "net": int(row[0]) - int(row[1]),
    }


def parse_rate(raw: str) -> Decimal:
    try:
        r = Decimal(raw)
    except (InvalidOperation, ValueError) as e:
        raise LedgerError(f"invalid FX rate {raw!r}") from e
    if not r.is_finite() or r <= 0:
        raise LedgerError(f"FX rate must be positive finite, got {raw!r}")
    return r
