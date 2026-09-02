"""Idempotency tests for POST /v1/transfers and POST /v1/journals.

Self-contained: builds its own engine/session/app inline (no conftest coupling).
"""

from __future__ import annotations

import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from ledgerline.api import create_app, get_db
from ledgerline.auth import new_api_key
from ledgerline.db import init_db
from ledgerline.models import Organization


def _make_client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    init_db(engine)
    SessionFactory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    raw, key_hash, prefix = new_api_key()
    db = SessionFactory()
    try:
        org = Organization(name="idem-org", api_key_hash=key_hash, key_prefix=prefix)
        db.add(org)
        db.commit()
    finally:
        db.close()

    app = create_app()

    def _override():
        session = SessionFactory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override
    client = TestClient(app)
    return client, {"X-API-Key": raw}


def _two_accounts(client, headers):
    suffix = uuid.uuid4().hex[:6]
    r1 = client.post(
        "/v1/accounts",
        json={"code": f"A{suffix}", "name": "Cash", "type": "asset", "currency": "USD"},
        headers=headers,
    )
    assert r1.status_code == 201, r1.text
    r2 = client.post(
        "/v1/accounts",
        json={"code": f"B{suffix}", "name": "Revenue", "type": "revenue", "currency": "USD"},
        headers=headers,
    )
    assert r2.status_code == 201, r2.text
    return r1.json()["id"], r2.json()["id"]


def _transfer_body(a, b, amount="10.00"):
    return {
        "debit_account_id": a,
        "credit_account_id": b,
        "amount": amount,
        "currency": "USD",
    }


def _journal_body(a, b, debit="10.00"):
    return {
        "currency": "USD",
        "description": "idem journal",
        "lines": [
            {"account_id": a, "debit": debit},
            {"account_id": b, "credit": debit},
        ],
    }


def test_transfers_same_key_same_body_replays_identical_id():
    client, headers = _make_client()
    a, b = _two_accounts(client, headers)
    key = uuid.uuid4().hex
    body = _transfer_body(a, b)
    h = {**headers, "Idempotency-Key": key}
    r1 = client.post("/v1/transfers", json=body, headers=h)
    assert r1.status_code == 201, r1.text
    r2 = client.post("/v1/transfers", json=body, headers=h)
    assert r2.status_code == 201, r2.text
    assert r2.json()["id"] == r1.json()["id"]
    # only one journal was actually created
    lst = client.get("/v1/journals", headers=headers)
    assert lst.status_code == 200
    assert len(lst.json()["data"]) == 1


def test_transfers_same_key_different_body_conflicts():
    client, headers = _make_client()
    a, b = _two_accounts(client, headers)
    key = uuid.uuid4().hex
    h = {**headers, "Idempotency-Key": key}
    r1 = client.post("/v1/transfers", json=_transfer_body(a, b, "10.00"), headers=h)
    assert r1.status_code == 201, r1.text
    r2 = client.post("/v1/transfers", json=_transfer_body(a, b, "99.99"), headers=h)
    assert r2.status_code == 409, r2.text


def test_journals_same_key_same_body_replays_identical_id():
    client, headers = _make_client()
    a, b = _two_accounts(client, headers)
    key = uuid.uuid4().hex
    body = _journal_body(a, b)
    h = {**headers, "Idempotency-Key": key}
    r1 = client.post("/v1/journals", json=body, headers=h)
    assert r1.status_code == 201, r1.text
    r2 = client.post("/v1/journals", json=body, headers=h)
    assert r2.status_code == 201, r2.text
    assert r2.json()["id"] == r1.json()["id"]


def test_journals_same_key_different_body_conflicts():
    client, headers = _make_client()
    a, b = _two_accounts(client, headers)
    key = uuid.uuid4().hex
    h = {**headers, "Idempotency-Key": key}
    r1 = client.post("/v1/journals", json=_journal_body(a, b, "10.00"), headers=h)
    assert r1.status_code == 201, r1.text
    r2 = client.post("/v1/journals", json=_journal_body(a, b, "11.00"), headers=h)
    assert r2.status_code == 409, r2.text


def test_short_idempotency_key_rejected_422():
    client, headers = _make_client()
    a, b = _two_accounts(client, headers)
    h = {**headers, "Idempotency-Key": "x"}
    r = client.post("/v1/journals", json=_journal_body(a, b), headers=h)
    assert r.status_code == 422, r.text
