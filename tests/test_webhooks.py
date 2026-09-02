"""Webhook tests: signing, enqueue fan-out, dispatch success/failure, URL policy.

Self-contained: builds its own engine/session/app inline (no conftest coupling).
HTTP for webhook dispatch is the only mocked boundary (monkeypatch httpx.post).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import sys
import uuid
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from ledgerline import webhooks as wh
from ledgerline.api import create_app, get_db
from ledgerline.auth import new_api_key
from ledgerline.db import init_db
from ledgerline.models import Organization, WebhookDelivery

SECRET = "s3cr3t-s3cr3t-1234"  # noqa: S105 — throwaway test-fixture credential, not a real secret


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
        org = Organization(name="wh-org", api_key_hash=key_hash, key_prefix=prefix)
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
    return client, {"X-API-Key": raw}, SessionFactory


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


def _add_endpoint(client, headers, url="https://example.com/hook"):
    r = client.post(
        "/v1/webhook-endpoints",
        json={"url": url, "secret": SECRET},
        headers=headers,
    )
    return r


def _deliveries(SessionFactory):
    db = SessionFactory()
    try:
        return db.execute(select(WebhookDelivery)).scalars().all()
    finally:
        db.close()


def test_sign_payload_deterministic():
    body = b'{"journal_id":"abc"}'
    got = wh.sign_payload(SECRET, body)
    expected = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert got == expected
    assert got.startswith("sha256=")
    # deterministic across calls
    assert wh.sign_payload(SECRET, body) == got


def test_enqueue_creates_deliveries_per_active_endpoint():
    client, headers, SessionFactory = _make_client()
    assert _add_endpoint(client, headers, "https://example.com/hook1").status_code == 201
    assert _add_endpoint(client, headers, "https://example.com/hook2").status_code == 201
    a, b = _two_accounts(client, headers)
    r = client.post(
        "/v1/transfers",
        json={
            "debit_account_id": a,
            "credit_account_id": b,
            "amount": "10.00",
            "currency": "USD",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    rows = _deliveries(SessionFactory)
    # one delivery per active endpoint for the journal.posted event
    assert len(rows) == 2
    assert all(d.status == "pending" for d in rows)


def test_dispatch_success_marks_delivered(monkeypatch):
    client, headers, SessionFactory = _make_client()
    assert _add_endpoint(client, headers).status_code == 201
    a, b = _two_accounts(client, headers)
    r = client.post(
        "/v1/transfers",
        json={
            "debit_account_id": a,
            "credit_account_id": b,
            "amount": "10.00",
            "currency": "USD",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text

    class _Resp:
        status_code = 200

    monkeypatch.setattr("ledgerline.webhooks.httpx.post", lambda *a, **k: _Resp())

    d = client.post("/v1/webhooks/dispatch", headers=headers)
    assert d.status_code == 200, d.text
    assert d.json()["delivered"] == 1
    rows = _deliveries(SessionFactory)
    assert len(rows) == 1
    assert rows[0].status == "delivered"
    assert rows[0].attempts == 1


def test_dispatch_failure_stays_pending_with_retry(monkeypatch):
    client, headers, SessionFactory = _make_client()
    assert _add_endpoint(client, headers).status_code == 201
    a, b = _two_accounts(client, headers)
    r = client.post(
        "/v1/transfers",
        json={
            "debit_account_id": a,
            "credit_account_id": b,
            "amount": "10.00",
            "currency": "USD",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text

    class _Resp:
        status_code = 500

    monkeypatch.setattr("ledgerline.webhooks.httpx.post", lambda *a, **k: _Resp())
    before = datetime.utcnow()

    d = client.post("/v1/webhooks/dispatch", headers=headers)
    assert d.status_code == 200, d.text
    rows = _deliveries(SessionFactory)
    assert len(rows) == 1
    assert rows[0].status == "pending"
    assert rows[0].attempts == 1
    assert rows[0].next_retry_at > before


def test_https_enforcement():
    client, headers, _ = _make_client()
    bad = _add_endpoint(client, headers, "http://example.com/hook")
    assert bad.status_code == 422, bad.text
    ok_local = _add_endpoint(client, headers, "http://localhost:8000/hook")
    assert ok_local.status_code == 201, ok_local.text
    ok_https = _add_endpoint(client, headers, "https://example.com/hook")
    assert ok_https.status_code == 201, ok_https.text
