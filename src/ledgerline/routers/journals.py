"""Journal + transfer endpoints (idempotent writes)."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerline import webhooks as wh
from ledgerline.deps import (
    get_db,
    idem_lookup,
    idem_replay_check,
    idem_store,
    is_nonzero_decimal,
    require_org,
    validate_idempotency_key,
)
from ledgerline.ledger import (
    JournalLine,
    UnbalancedJournalError,
    post_journal,
    reverse_journal,
)
from ledgerline.models import Entry, Journal
from ledgerline.money import normalize_currency, parse_amount_to_minor
from ledgerline.schemas import JournalCreate, TransferCreate

router = APIRouter(tags=["journals"])


def _to_lines(body: JournalCreate) -> list[JournalLine]:
    cur = normalize_currency(body.currency)
    if not body.lines:
        raise UnbalancedJournalError("journal requires at least 2 entry lines")
    out = []
    for ln in body.lines:
        d = parse_amount_to_minor(ln.debit, cur) if is_nonzero_decimal(ln.debit) else 0
        c = parse_amount_to_minor(ln.credit, cur) if is_nonzero_decimal(ln.credit) else 0
        out.append(
            JournalLine(account_id=ln.account_id, debit_minor=d, credit_minor=c, memo=ln.memo)
        )
    return out


@router.post("/v1/journals", status_code=201, response_model=None)
def create_journal(
    body: JournalCreate,
    request: Request,
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any] | JSONResponse:
    org = require_org(request, db)
    validate_idempotency_key(idempotency_key)
    raw = json.dumps(body.model_dump(), sort_keys=True).encode()
    if idempotency_key:
        rec = idem_lookup(db, org.id, idempotency_key)
        if rec is not None:
            idem_replay_check(raw, rec)
            return JSONResponse(
                status_code=rec.response_status, content=json.loads(rec.response_body)
            )
    lines = _to_lines(body)
    j = post_journal(
        db,
        org_id=org.id,
        lines=lines,
        currency=body.currency,
        description=body.description,
        reference=body.reference,
        idempotency_key=idempotency_key,
    )
    db.flush()
    wh.enqueue(
        db,
        org_id=org.id,
        event="journal.posted",
        payload={"journal_id": j.id, "org_id": org.id, "currency": j.currency},
    )
    resp = {
        "id": j.id,
        "status": j.status,
        "currency": j.currency,
        "posted_at": j.posted_at.isoformat(),
    }
    if idempotency_key:
        idem_store(
            db,
            org_id=org.id,
            key=idempotency_key,
            method="POST",
            path="/v1/journals",
            body=raw,
            status=201,
            resp=resp,
            journal_id=j.id,
        )
    return resp


@router.post("/v1/transfers", status_code=201, response_model=None)
def create_transfer(
    body: TransferCreate,
    request: Request,
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any] | JSONResponse:
    org = require_org(request, db)
    validate_idempotency_key(idempotency_key)
    raw = json.dumps(body.model_dump(), sort_keys=True).encode()
    if idempotency_key:
        rec = idem_lookup(db, org.id, idempotency_key)
        if rec is not None:
            idem_replay_check(raw, rec)
            return JSONResponse(
                status_code=rec.response_status, content=json.loads(rec.response_body)
            )
    if body.debit_account_id == body.credit_account_id:
        raise HTTPException(status_code=422, detail="debit and credit accounts must differ")
    cur = normalize_currency(body.currency)
    minor = parse_amount_to_minor(body.amount, cur)
    lines = [
        JournalLine(account_id=body.debit_account_id, debit_minor=minor),
        JournalLine(account_id=body.credit_account_id, credit_minor=minor),
    ]
    j = post_journal(
        db,
        org_id=org.id,
        lines=lines,
        currency=cur,
        description=body.description,
        reference=body.reference,
        idempotency_key=idempotency_key,
    )
    db.flush()
    wh.enqueue(
        db,
        org_id=org.id,
        event="journal.posted",
        payload={"journal_id": j.id, "org_id": org.id, "currency": j.currency},
    )
    resp = {"id": j.id, "status": j.status, "currency": j.currency, "minor": minor}
    if idempotency_key:
        idem_store(
            db,
            org_id=org.id,
            key=idempotency_key,
            method="POST",
            path="/v1/transfers",
            body=raw,
            status=201,
            resp=resp,
            journal_id=j.id,
        )
    return resp


@router.get("/v1/journals")
def list_journals(
    request: Request,
    db: Session = Depends(get_db),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    org = require_org(request, db)
    rows = (
        db.execute(
            select(Journal)
            .where(Journal.org_id == org.id)
            .order_by(Journal.posted_at.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return {
        "data": [
            {
                "id": j.id,
                "status": j.status,
                "currency": j.currency,
                "description": j.description,
                "reference": j.reference,
                "posted_at": j.posted_at.isoformat(),
            }
            for j in rows
        ]
    }


@router.get("/v1/journals/{journal_id}")
def get_journal(journal_id: str, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    org = require_org(request, db)
    j = db.execute(
        select(Journal).where(Journal.id == journal_id, Journal.org_id == org.id)
    ).scalar_one_or_none()
    if j is None:
        raise HTTPException(status_code=404, detail="journal not found")
    entries = db.execute(select(Entry).where(Entry.journal_id == j.id)).scalars().all()
    return {
        "id": j.id,
        "status": j.status,
        "currency": j.currency,
        "description": j.description,
        "reference": j.reference,
        "reversal_of_id": j.reversal_of_id,
        "reversed_by_id": j.reversed_by_id,
        "entries": [
            {
                "account_id": e.account_id,
                "debit_minor": e.debit_minor,
                "credit_minor": e.credit_minor,
                "memo": e.memo,
            }
            for e in entries
        ],
    }


@router.post("/v1/journals/{journal_id}/reverse", status_code=201)
def reverse(journal_id: str, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    org = require_org(request, db)
    rev = reverse_journal(db, org_id=org.id, journal_id=journal_id)
    db.flush()
    wh.enqueue(
        db,
        org_id=org.id,
        event="journal.reversed",
        payload={"journal_id": rev.id, "reversal_of": journal_id},
    )
    return {"id": rev.id, "reversal_of": journal_id, "status": rev.status}
