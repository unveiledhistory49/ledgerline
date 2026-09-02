import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src"))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

import pytest
from fastapi.testclient import TestClient


def make_org(db, name="Test Org"):
    """Create an Organization with a fresh API key. Returns (org, raw_key)."""
    from ledgerline.auth import new_api_key
    from ledgerline.models import Organization

    raw, key_hash, prefix = new_api_key()
    org = Organization(name=name, api_key_hash=key_hash, key_prefix=prefix)
    db.add(org)
    db.flush()
    return org, raw


def make_account(db, org_id, code, name, type, currency="USD"):
    """Create an Account. Returns the Account (flushed)."""
    from ledgerline.models import Account, AccountType

    if isinstance(type, AccountType):
        t = type.value
    else:
        t = AccountType(str(type).lower()).value
    cur = str(currency).strip().upper()
    acc = Account(org_id=org_id, code=code, name=name, type=t, currency=cur)
    db.add(acc)
    db.flush()
    return acc


@pytest.fixture
def engine():
    from ledgerline.db import create_app_engine, init_db

    eng = create_app_engine("sqlite:///:memory:")
    init_db(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    from ledgerline.db import create_session_factory

    SessionFactory = create_session_factory(engine)
    sess = SessionFactory()
    yield sess
    sess.close()


@pytest.fixture
def session(db):
    return db


@pytest.fixture
def api_client(db):
    """Build FastAPI app with get_db overridden to use the test session.

    Returns the TestClient with extra attrs: org, raw_key, headers,
    auth_headers (X-API-Key header helper).
    """
    from ledgerline import api as api_mod

    org, raw_key = make_org(db, name="API Org")
    db.commit()

    app = api_mod.create_app()

    def _override():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    app.dependency_overrides[api_mod.get_db] = _override
    client = TestClient(app)
    headers = {"X-API-Key": raw_key}
    client.org = org  # type: ignore[attr-defined]
    client.raw_key = raw_key  # type: ignore[attr-defined]
    client.headers_helper = headers  # type: ignore[attr-defined]
    client.auth_headers = headers  # type: ignore[attr-defined]

    def _auth_headers(key=None):
        return {"X-API-Key": key or raw_key}

    client.auth_headers_fn = _auth_headers  # type: ignore[attr-defined]
    return client
