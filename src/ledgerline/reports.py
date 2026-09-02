"""Financial reports computed directly from entries (no cached balances to drift)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ledgerline.models import Account, AccountType, Entry, Journal, JournalStatus

POSTED_STATUSES = (JournalStatus.POSTED.value, JournalStatus.REVERSED.value)


def trial_balance(db: Session, *, org_id: str) -> dict[str, Any]:
    """Every debit has a credit: sum of all nets must be exactly zero."""
    rows = db.execute(
        select(
            Account.id,
            Account.code,
            Account.name,
            Account.type,
            Account.currency,
            func.coalesce(func.sum(Entry.debit_minor), 0).label("d"),
            func.coalesce(func.sum(Entry.credit_minor), 0).label("c"),
        )
        .outerjoin(Entry, (Entry.account_id == Account.id) & (Entry.org_id == org_id))
        .outerjoin(
            Journal,
            (Journal.id == Entry.journal_id) & (Journal.status.in_(POSTED_STATUSES)),
        )
        .where(Account.org_id == org_id)
        .group_by(Account.id)
        .order_by(Account.code)
    ).all()
    lines = []
    total_d = total_c = 0
    for acc_id, code, name, typ, ccy, d, c in rows:
        d, c = int(d or 0), int(c or 0)
        # Only count posted entries: the outer join above includes NULL journal rows;
        # recompute per-account posted totals explicitly to avoid voided leakage.
        posted = db.execute(
            select(
                func.coalesce(func.sum(Entry.debit_minor), 0),
                func.coalesce(func.sum(Entry.credit_minor), 0),
            )
            .select_from(Entry)
            .join(Journal, Journal.id == Entry.journal_id)
            .where(
                Entry.org_id == org_id,
                Entry.account_id == acc_id,
                Journal.status.in_(POSTED_STATUSES),
            )
        ).one()
        pd, pc = int(posted[0]), int(posted[1])
        lines.append(
            {
                "account_id": acc_id,
                "code": code,
                "name": name,
                "type": typ,
                "currency": ccy,
                "debit": pd,
                "credit": pc,
                "net": pd - pc,
            }
        )
        total_d += pd
        total_c += pc
    balanced = total_d == total_c
    digest = hashlib.sha256(json.dumps(lines, sort_keys=True).encode()).hexdigest()
    return {
        "lines": lines,
        "total_debit": total_d,
        "total_credit": total_c,
        "balanced": balanced,
        "hash": digest,
    }


def balance_sheet(db: Session, *, org_id: str) -> dict[str, Any]:
    tb = trial_balance(db, org_id=org_id)
    buckets: dict[str, int] = {"asset": 0, "liability": 0, "equity": 0, "revenue": 0, "expense": 0}
    for ln in tb["lines"]:
        # Net debit-positive; flip sign for credit-normal accounts to get statement-positive totals.
        val = (
            ln["net"]
            if ln["type"] in (AccountType.ASSET.value, AccountType.EXPENSE.value)
            else -ln["net"]
        )
        buckets[ln["type"]] += val
    assets = buckets["asset"]
    liabilities = buckets["liability"]
    equity = buckets["equity"] + buckets["revenue"] - buckets["expense"]
    return {
        "assets": assets,
        "liabilities": liabilities,
        "equity": equity,
        "net_income": buckets["revenue"] - buckets["expense"],
        "balances": assets == liabilities + equity,
        "by_type": buckets,
    }


def income_statement(db: Session, *, org_id: str) -> dict[str, int]:
    tb = trial_balance(db, org_id=org_id)
    revenue = sum(-ln["net"] for ln in tb["lines"] if ln["type"] == AccountType.REVENUE.value)
    expenses = sum(ln["net"] for ln in tb["lines"] if ln["type"] == AccountType.EXPENSE.value)
    return {"revenue": revenue, "expenses": expenses, "net_income": revenue - expenses}
