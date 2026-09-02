"""Read-only financial reports."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ledgerline import reports as reports_mod
from ledgerline.deps import get_db, require_org

router = APIRouter(prefix="/v1/reports", tags=["reports"])


@router.get("/trial-balance")
def trial_balance(request: Request, db: Session = Depends(get_db)):  # type: ignore[no-untyped-def]
    org = require_org(request, db)
    return reports_mod.trial_balance(db, org_id=org.id)


@router.get("/balance-sheet")
def balance_sheet(request: Request, db: Session = Depends(get_db)):  # type: ignore[no-untyped-def]
    org = require_org(request, db)
    return reports_mod.balance_sheet(db, org_id=org.id)


@router.get("/income-statement")
def income_statement(request: Request, db: Session = Depends(get_db)):  # type: ignore[no-untyped-def]
    org = require_org(request, db)
    return reports_mod.income_statement(db, org_id=org.id)
