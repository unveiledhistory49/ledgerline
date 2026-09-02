"""End-to-end API tests via TestClient with an isolated in-memory SQLite DB.

Self-contained: builds its own engine/session/app inline (no conftest coupling).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from ledgerline.api import create_app, get_db
from ledgerline.auth import new_api_key
from ledgerline.db import init_db
from ledgerline.models import Organization


def _make_client():
    """Create an isolated app + DB + org, returning (client, headers)."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    init_db(engine)
    from sqlalchemy.orm import sessionmaker

    SessionFactory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    raw, key_hash, prefix = new_api_key()
    db = SessionFactory()
    try:
        org = Organization(name="test-org", api_key_hash=key_hash, key_prefix=prefix)
        db.add(org)
        db.commit()
        db.refresh(org)
        org_id = org.id
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
    headers = {"X-API-Key": raw}
    return client, headers, org_id


def _create_account(client, headers, code, name="Test", type="asset", currency="USD"):
    r = client.post(
        "/v1/accounts",
        json={"code": code, "name": name, "type": type, "currency": currency},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _two_accounts(client, headers, suffix=""):
    a = _create_account(client, headers, code=f"1000{suffix}", name="Cash", type="asset")
    b = _create_account(client, headers, code=f"4000{suffix}", name="Revenue", type="revenue")
    return a, b


def test_unauthorized_without_key():
    client, headers, _ = _make_client()
    r = client.get("/v1/accounts")
    assert r.status_code == 401
    r = client.post(
        "/v1/accounts",
        json={"code": "X", "name": "X", "type": "asset", "currency": "USD"},
    )
    assert r.status_code == 401
    # wrong key also 401
    r = client.get("/v1/accounts", headers={"X-API-Key": "ll_live_bogus"})
    assert r.status_code == 401


def test_create_and_list_accounts():
    client, headers, _ = _make_client()
    acc = _create_account(client, headers, code="1000", name="Cash")
    assert acc["code"] == "1000"
    assert "id" in acc
    r = client.get("/v1/accounts", headers=headers)
    assert r.status_code == 200, r.text
    codes = [a["code"] for a in r.json()["data"]]
    assert "1000" in codes


def test_transfer_then_balance_check():
    client, headers, _ = _make_client()
    a, b = _two_accounts(client, headers)
    r = client.post(
        "/v1/transfers",
        json={
            "debit_account_id": a["id"],
            "credit_account_id": b["id"],
            "amount": "25.50",
            "currency": "USD",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    journal_id = r.json()["id"]
    assert journal_id

    rb = client.get(f"/v1/accounts/{a['id']}/balance", headers=headers)
    assert rb.status_code == 200, rb.text
    assert rb.json()["net"] == 2550

    rb2 = client.get(f"/v1/accounts/{b['id']}/balance", headers=headers)
    assert rb2.status_code == 200, rb2.text
    assert rb2.json()["net"] == -2550


def test_journal_detail_and_list():
    client, headers, _ = _make_client()
    a, b = _two_accounts(client, headers)
    r = client.post(
        "/v1/transfers",
        json={
            "debit_account_id": a["id"],
            "credit_account_id": b["id"],
            "amount": "10.00",
            "currency": "USD",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    jid = r.json()["id"]

    d = client.get(f"/v1/journals/{jid}", headers=headers)
    assert d.status_code == 200, d.text
    body = d.json()
    assert body["id"] == jid
    assert len(body["entries"]) == 2

    lst = client.get("/v1/journals", headers=headers)
    assert lst.status_code == 200, lst.text
    ids = [j["id"] for j in lst.json()["data"]]
    assert jid in ids


def test_reverse_endpoint():
    client, headers, _ = _make_client()
    a, b = _two_accounts(client, headers)
    r = client.post(
        "/v1/transfers",
        json={
            "debit_account_id": a["id"],
            "credit_account_id": b["id"],
            "amount": "10.00",
            "currency": "USD",
        },
        headers=headers,
    )
    jid = r.json()["id"]
    rev = client.post(f"/v1/journals/{jid}/reverse", headers=headers)
    assert rev.status_code == 201, rev.text
    assert rev.json()["reversal_of"] == jid
    # original now marked reversed
    d = client.get(f"/v1/journals/{jid}", headers=headers)
    assert d.status_code == 200
    assert d.json()["status"] == "reversed"


def test_reports_endpoints_balanced():
    client, headers, _ = _make_client()
    a, b = _two_accounts(client, headers)
    r = client.post(
        "/v1/transfers",
        json={
            "debit_account_id": a["id"],
            "credit_account_id": b["id"],
            "amount": "100.00",
            "currency": "USD",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text

    tb = client.get("/v1/reports/trial-balance", headers=headers)
    assert tb.status_code == 200, tb.text
    assert tb.json()["balanced"] is True
    assert tb.json()["total_debit"] == tb.json()["total_credit"]

    bs = client.get("/v1/reports/balance-sheet", headers=headers)
    assert bs.status_code == 200, bs.text
    assert bs.json()["balances"] is True

    is_ = client.get("/v1/reports/income-statement", headers=headers)
    assert is_.status_code == 200, is_.text
    assert "net_income" in is_.json()


def test_double_entry_via_api_stays_balanced():
    client, headers, _ = _make_client()
    a, b = _two_accounts(client, headers)
    for amt in ("5.00", "7.25"):
        r = client.post(
            "/v1/transfers",
            json={
                "debit_account_id": a["id"],
                "credit_account_id": b["id"],
                "amount": amt,
                "currency": "USD",
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text
    # direct journal post as well (balanced double-entry)
    r = client.post(
        "/v1/journals",
        json={
            "currency": "USD",
            "description": "manual",
            "lines": [
                {"account_id": a["id"], "debit": "3.00"},
                {"account_id": b["id"], "credit": "3.00"},
            ],
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text

    tb = client.get("/v1/reports/trial-balance", headers=headers)
    assert tb.status_code == 200, tb.text
    data = tb.json()
    assert data["balanced"] is True
    assert data["total_debit"] == data["total_credit"]
    assert data["total_debit"] > 0
