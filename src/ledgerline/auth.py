"""API-key auth: keys look like ll_live_<32 hex>, only sha256 hashes are stored."""

from __future__ import annotations

import hashlib
import hmac
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerline.config import settings
from ledgerline.models import Organization


def hash_key(raw: str) -> str:
    return hashlib.sha256((settings.api_key_pepper + "::" + raw).encode()).hexdigest()


def new_api_key() -> tuple[str, str, str]:
    raw = "ll_live_" + secrets.token_hex(16)
    return raw, hash_key(raw), raw[-8:]


def authenticate(db: Session, raw_key: str) -> Organization | None:
    if not raw_key or not raw_key.startswith("ll_live_"):
        return None
    want = hash_key(raw_key)
    # Hash column is unique; fetch then constant-time compare to avoid subtle
    # timing oracle on prefix scans (paranoia is cheap here).
    rows = db.execute(select(Organization)).scalars().all()
    for org in rows:
        if hmac.compare_digest(org.api_key_hash, want):
            return org
    return None
