"""Money helpers. All ledger amounts are integers in minor units — never float."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CURRENCY_EXPONENTS: dict[str, int] = {
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "CAD": 2,
    "AUD": 2,
    "CHF": 2,
    "CNY": 2,
    "INR": 2,
    "BRL": 2,
    "MXN": 2,
    "SGD": 2,
    "HKD": 2,
    "JPY": 0,
    "KRW": 0,
    "VND": 0,
    "CLP": 0,
    "KWD": 3,
    "BHD": 3,
    "OMR": 3,
    "JOD": 3,
}

MAX_MINOR = 10**15  # ~$10T in cents; overflow guard


class MoneyError(ValueError):
    pass


def normalize_currency(code: str) -> str:
    c = code.strip().upper()
    if c not in CURRENCY_EXPONENTS:
        raise MoneyError(f"unsupported currency: {code!r}")
    return c


def parse_amount_to_minor(amount: str | int | Decimal, currency: str) -> int:
    """Parse human amount ('10.25', Decimal, or int minor passthrough is NOT allowed)
    into integer minor units. Strings only — callers must be explicit."""
    cur = normalize_currency(currency)
    exp = CURRENCY_EXPONENTS[cur]
    try:
        d = Decimal(str(amount).strip())
    except (InvalidOperation, ValueError, AttributeError) as e:
        raise MoneyError(f"invalid amount {amount!r}: {e}") from e
    if not d.is_finite():
        raise MoneyError(f"invalid amount {amount!r}: not finite")
    if d <= 0:
        raise MoneyError(f"amount must be > 0, got {amount!r}")
    quantum = Decimal(1) if exp == 0 else Decimal(1).scaleb(-exp)
    # Reject values with more precision than the currency allows
    # (e.g. $10.251 for USD, ¥100.5 for JPY) instead of silently rounding.
    if d != d.quantize(quantum, rounding=ROUND_HALF_UP):
        raise MoneyError(f"amount {amount!r} has excess precision for {cur}")
    minor = int((d / quantum).to_integral_value(rounding=ROUND_HALF_UP))
    if minor <= 0:
        raise MoneyError(f"amount {amount!r} rounds to zero minor units for {cur}")
    if minor > MAX_MINOR:
        raise MoneyError(f"amount {amount!r} exceeds maximum ledger value")
    return minor


def format_minor(minor: int, currency: str) -> str:
    cur = normalize_currency(currency)
    exp = CURRENCY_EXPONENTS[cur]
    if exp == 0:
        return f"{minor} {cur}"
    major = Decimal(minor).scaleb(-exp)
    return f"{major:.{exp}f} {cur}"


def convert_minor(minor: int, rate: Decimal, target_exp: int) -> int:
    """Convert minor units at a Decimal FX rate into target minor units (HALF_UP)."""
    if rate <= 0:
        raise MoneyError(f"FX rate must be > 0, got {rate}")
    if minor <= 0:
        raise MoneyError("convert amount must be > 0")
    result = int((Decimal(minor) * rate).to_integral_value(rounding=ROUND_HALF_UP))
    if result <= 0:
        raise MoneyError("FX conversion rounds to zero")
    if result > MAX_MINOR:
        raise MoneyError("FX conversion exceeds maximum ledger value")
    return result
