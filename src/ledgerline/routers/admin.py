"""FX rates, period close, and webhook administration."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerline import fx as fx_mod
from ledgerline import reports as reports_mod
from ledgerline import webhooks as wh
from ledgerline.deps import get_db, require_org
from ledgerline.models import PeriodClose, WebhookEndpoint
from ledgerline.schemas import FxRateCreate, PeriodCloseCreate, WebhookEndpointCreate

router = APIRouter(tags=["admin"])


@router.post("/v1/fx-rates", status_code=201)
def set_fx(body: FxRateCreate, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    require_org(request, db)
    row = fx_mod.set_rate(db, base=body.base, quote=body.quote, rate=body.rate)
    return {"base": row.base, "quote": row.quote, "rate": row.rate}


@router.get("/v1/fx-convert")
def convert(
    amount_minor: int, base: str, quote: str, request: Request, db: Session = Depends(get_db)
) -> dict[str, Any]:
    require_org(request, db)
    return fx_mod.preview_conversion(db, minor=amount_minor, base=base, quote=quote)


@router.post("/v1/periods/close", status_code=201)
def close_period(
    body: PeriodCloseCreate, request: Request, db: Session = Depends(get_db)
) -> dict[str, Any]:
    org = require_org(request, db)
    exists = db.execute(
        select(PeriodClose).where(PeriodClose.org_id == org.id, PeriodClose.period == body.period)
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail="period already closed")
    tb_data = reports_mod.trial_balance(db, org_id=org.id)
    if not tb_data["balanced"]:
        raise HTTPException(status_code=422, detail="cannot close period with unbalanced ledger")
    row = PeriodClose(org_id=org.id, period=body.period, trial_balance_hash=tb_data["hash"])
    db.add(row)
    db.flush()
    return {"period": row.period, "hash": row.trial_balance_hash}


@router.post("/v1/webhook-endpoints", status_code=201)
def add_endpoint(
    body: WebhookEndpointCreate, request: Request, db: Session = Depends(get_db)
) -> dict[str, Any]:
    org = require_org(request, db)
    if not (body.url.startswith("https://") or body.url.startswith("http://localhost")):
        raise HTTPException(
            status_code=422,
            detail="webhook URL must be https (http allowed only for localhost)",
        )
    ep = WebhookEndpoint(org_id=org.id, url=body.url, secret=body.secret, active=1)
    db.add(ep)
    db.flush()
    return {"id": ep.id, "url": ep.url}


@router.post("/v1/webhooks/dispatch")
def dispatch(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    require_org(request, db)
    return wh.dispatch_due(db)
