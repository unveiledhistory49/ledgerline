"""Account endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerline.deps import get_db, require_org
from ledgerline.ledger import account_balances
from ledgerline.models import Account, AccountType
from ledgerline.money import normalize_currency
from ledgerline.schemas import AccountCreate

router = APIRouter(prefix="/v1/accounts", tags=["accounts"])


@router.post("", status_code=201)
def create_account(
    body: AccountCreate, request: Request, db: Session = Depends(get_db)
) -> dict[str, Any]:
    org = require_org(request, db)
    try:
        typ = AccountType(body.type.lower())
    except ValueError:
        raise HTTPException(status_code=422, detail=f"invalid account type {body.type!r}") from None
    cur = normalize_currency(body.currency)
    exists = db.execute(
        select(Account).where(Account.org_id == org.id, Account.code == body.code)
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail=f"account code {body.code} already exists")
    acc = Account(
        org_id=org.id,
        code=body.code.strip(),
        name=body.name.strip(),
        type=typ.value,
        currency=cur,
    )
    db.add(acc)
    db.flush()
    return {
        "id": acc.id,
        "code": acc.code,
        "name": acc.name,
        "type": acc.type,
        "currency": acc.currency,
    }


@router.get("")
def list_accounts(
    request: Request,
    db: Session = Depends(get_db),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    org = require_org(request, db)
    rows = (
        db.execute(
            select(Account)
            .where(Account.org_id == org.id)
            .order_by(Account.code)
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return {
        "data": [
            {
                "id": a.id,
                "code": a.code,
                "name": a.name,
                "type": a.type,
                "currency": a.currency,
                "locked": bool(a.is_locked),
            }
            for a in rows
        ]
    }


@router.get("/{account_id}/balance")
def acc_balance(account_id: str, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    org = require_org(request, db)
    acc = db.get(Account, account_id)
    if acc is None or acc.org_id != org.id:
        raise HTTPException(status_code=404, detail="account not found")
    return {
        "account_id": acc.id,
        "code": acc.code,
        **account_balances(db, org_id=org.id, account_id=acc.id),
    }
