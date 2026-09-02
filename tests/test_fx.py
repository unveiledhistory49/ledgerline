import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src"))
sys.path.insert(0, "/root/ledgerline/src")

from decimal import Decimal

import pytest
from conftest import make_org

from ledgerline import fx as fx_mod
from ledgerline.ledger import LedgerError
from ledgerline.money import MoneyError, format_minor, parse_amount_to_minor


def test_set_get_roundtrip(db):
    org, _ = make_org(db, name="FX Org 1")
    fx_mod.set_rate(db, base="USD", quote="EUR", rate="1.25")
    db.flush()
    assert fx_mod.get_rate(db, base="USD", quote="EUR") == Decimal("1.25")


def test_get_rate_missing_raises(db):
    org, _ = make_org(db, name="FX Org 2")
    with pytest.raises(LookupError):
        fx_mod.get_rate(db, base="GBP", quote="CAD")


def test_same_currency_returns_one(db):
    org, _ = make_org(db, name="FX Org 3")
    assert fx_mod.get_rate(db, base="USD", quote="USD") == Decimal(1)
    assert fx_mod.get_rate(db, base="jpy", quote="JPY") == Decimal(1)


def test_preview_conversion_math_by_hand(db):
    org, _ = make_org(db, name="FX Org 4")
    fx_mod.set_rate(db, base="USD", quote="EUR", rate="0.9")
    db.flush()
    # 10000 minor ($100.00) * 0.9 = 9000 minor by hand.
    out = fx_mod.preview_conversion(db, minor=10000, base="USD", quote="EUR")
    assert out["out_minor"] == "9000"
    assert Decimal(out["rate"]) == Decimal("0.9")
    # Second hand-check: 101 * 0.333 = 33.633 -> HALF_UP 34.
    fx_mod.set_rate(db, base="USD", quote="EUR", rate="0.333")
    db.flush()
    out2 = fx_mod.preview_conversion(db, minor=101, base="USD", quote="EUR")
    assert out2["out_minor"] == "34"


def test_invalid_rate_raises(db):
    org, _ = make_org(db, name="FX Org 5")
    with pytest.raises(LedgerError):
        fx_mod.set_rate(db, base="USD", quote="EUR", rate="0")
    with pytest.raises(LedgerError):
        fx_mod.set_rate(db, base="USD", quote="EUR", rate="-1.5")
    with pytest.raises(LedgerError):
        fx_mod.set_rate(db, base="USD", quote="EUR", rate="not-a-number")
    with pytest.raises(LedgerError):
        fx_mod.set_rate(db, base="USD", quote="EUR", rate="NaN")
    with pytest.raises(ValueError):
        fx_mod.set_rate(db, base="USD", quote="USD", rate="1.0")


def test_jpy_exponent_handling(db):
    org, _ = make_org(db, name="FX Org 6")
    # JPY exponent 0: whole yen only.
    assert parse_amount_to_minor("100", "JPY") == 100
    with pytest.raises(MoneyError):
        parse_amount_to_minor("100.5", "JPY")
    assert format_minor(100, "JPY") == "100 JPY"
    # USD exponent 2 for contrast.
    assert parse_amount_to_minor("100", "USD") == 10000
    assert format_minor(10000, "USD") == "100.00 USD"
    # FX into JPY: 10000 minor USD * 150 -> 1500000 minor JPY by hand.
    fx_mod.set_rate(db, base="USD", quote="JPY", rate="150")
    db.flush()
    out = fx_mod.preview_conversion(db, minor=10000, base="USD", quote="JPY")
    assert out["out_minor"] == "1500000"
