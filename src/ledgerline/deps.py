"""Shared FastAPI dependencies: engine/session, auth, idempotency helpers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from typing import Any

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ledgerline.auth import authenticate
from ledgerline.config import settings
from ledgerline.db import create_app_engine, create_session_factory, init_db
from ledgerline.models import IdempotencyRecord, Organization

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _SessionFactory
    if _engine is None:
        _engine = create_app_engine(settings.database_url)
        init_db(_engine)
        _SessionFactory = create_session_factory(_engine)
    return _engine


def get_db() -> Iterator[Session]:
    get_engine()
    assert _SessionFactory is not None
    db = _SessionFactory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def require_org(request: Request, db: Session = Depends(get_db)) -> Organization:
    key = request.headers.get("x-api-key", "")
    org = authenticate(db, key)
    if org is None:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")
    request.state.org_id = org.id
    return org


def idem_lookup(db: Session, org_id: str, key: str | None) -> IdempotencyRecord | None:
    if not key:
        return None
    return db.execute(
        select(IdempotencyRecord).where(
            IdempotencyRecord.org_id == org_id, IdempotencyRecord.key == key
        )
    ).scalar_one_or_none()


def idem_replay_check(raw: bytes, rec: IdempotencyRecord) -> None:
    if rec.request_hash != hashlib.sha256(raw).hexdigest():
        raise HTTPException(
            status_code=409, detail="Idempotency-Key already used with different payload"
        )


def idem_store(
    db: Session,
    *,
    org_id: str,
    key: str,
    method: str,
    path: str,
    body: bytes,
    status: int,
    resp: dict[str, Any],
    journal_id: str | None,
) -> None:
    db.add(
        IdempotencyRecord(
            org_id=org_id,
            key=key,
            method=method,
            path=path,
            request_hash=hashlib.sha256(body).hexdigest(),
            response_status=status,
            response_body=json.dumps(resp),
            journal_id=journal_id,
        )
    )
    db.flush()


def validate_idempotency_key(key: str | None) -> None:
    if key is not None and not 8 <= len(key) <= 64:
        raise HTTPException(status_code=422, detail="Idempotency-Key must be 8..64 chars")


def is_nonzero_decimal(raw: str) -> bool:
    from decimal import Decimal, InvalidOperation

    try:
        return Decimal(raw.strip()) != 0
    except (InvalidOperation, ValueError, AttributeError):
        raise HTTPException(status_code=422, detail=f"invalid decimal amount {raw!r}") from None
