"""FX rates + conversion preview (posting itself stays single-currency; see ADR-0003)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerline.ledger import parse_rate
from ledgerline.models import FxRate
from ledgerline.money import convert_minor, normalize_currency


def set_rate(
    db: Session, *, base: str, quote: str, rate: str, effective_at: datetime | None = None
) -> FxRate:
    b = normalize_currency(base)
    q = normalize_currency(quote)
    if b == q:
        raise ValueError("base and quote must differ")
    r = parse_rate(rate)
    row = FxRate(base=b, quote=q, rate=str(r), effective_at=effective_at or datetime.utcnow())
    db.add(row)
    db.flush()
    return row


def get_rate(db: Session, *, base: str, quote: str, at: datetime | None = None) -> Decimal:
    b = normalize_currency(base)
    q = normalize_currency(quote)
    if b == q:
        return Decimal(1)
    at = at or datetime.utcnow()
    row = db.execute(
        select(FxRate)
        .where(FxRate.base == b, FxRate.quote == q, FxRate.effective_at <= at)
        .order_by(FxRate.effective_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        raise LookupError(f"no FX rate {b}->{q} at {at.isoformat()}")
    return parse_rate(row.rate)


def preview_conversion(
    db: Session, *, minor: int, base: str, quote: str, at: datetime | None = None
) -> dict[str, str]:
    from ledgerline.money import CURRENCY_EXPONENTS

    rate = get_rate(db, base=base, quote=quote, at=at)
    target_exp = CURRENCY_EXPONENTS[normalize_currency(quote)]
    out = convert_minor(minor, rate, target_exp)
    return {"rate": str(rate), "out_minor": str(out)}
