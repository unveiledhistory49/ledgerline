"""Webhook outbox: HMAC-signed deliveries with exponential-backoff retries."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerline.config import settings
from ledgerline.models import WebhookDelivery, WebhookEndpoint

RETRY_DELAYS = [60, 300, 900, 3600, 10800, 21600, 43200, 86400]


def sign_payload(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def enqueue(db: Session, *, org_id: str, event: str, payload: dict[str, Any]) -> int:
    endpoints = (
        db.execute(
            select(WebhookEndpoint).where(
                WebhookEndpoint.org_id == org_id, WebhookEndpoint.active == 1
            )
        )
        .scalars()
        .all()
    )
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    n = 0
    for ep in endpoints:
        db.add(
            WebhookDelivery(
                endpoint_id=ep.id,
                org_id=org_id,
                event=event,
                payload=body,
                status="pending",
                attempts=0,
                next_retry_at=datetime.utcnow(),
            )
        )
        n += 1
    db.flush()
    return n


def dispatch_due(db: Session, *, limit: int = 25) -> dict[str, int]:
    now = datetime.utcnow()
    due = (
        db.execute(
            select(WebhookDelivery)
            .where(WebhookDelivery.status == "pending", WebhookDelivery.next_retry_at <= now)
            .limit(limit)
        )
        .scalars()
        .all()
    )
    ok = failed = 0
    for d in due:
        ep = db.get(WebhookEndpoint, d.endpoint_id)
        if ep is None or not ep.active:
            d.status = "dead"
            continue
        body = d.payload.encode()
        sig = sign_payload(ep.secret, body)
        d.attempts += 1
        try:
            r = httpx.post(
                ep.url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-LedgerLine-Event": d.event,
                    "X-LedgerLine-Signature": sig,
                },
                timeout=settings.webhook_timeout_seconds,
            )
            if 200 <= r.status_code < 300:
                d.status = "delivered"
                ok += 1
            else:
                raise RuntimeError(f"HTTP {r.status_code}")
        except Exception:
            if d.attempts >= settings.webhook_max_attempts:
                d.status = "dead"
            else:
                delay = RETRY_DELAYS[min(d.attempts - 1, len(RETRY_DELAYS) - 1)]
                d.next_retry_at = now + timedelta(seconds=delay)
            failed += 1
    db.flush()
    return {"delivered": ok, "pending_or_dead": failed}
