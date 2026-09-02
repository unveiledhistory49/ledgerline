import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src"))
sys.path.insert(0, "/root/ledgerline/src")

import pytest
from conftest import make_account, make_org

from ledgerline.ledger import (
    AccountLockedError,
    JournalLine,
    LedgerError,
    UnbalancedJournalError,
    post_journal,
)
from ledgerline.money import MoneyError, parse_amount_to_minor
from ledgerline.reports import trial_balance


def _two_accounts(db, org_id, currency="USD"):
    a = make_account(db, org_id, "1000", "Cash", "asset", currency)
    b = make_account(db, org_id, "3000", "Equity", "equity", currency)
    return a, b


def test_unbalanced_journal_rejected(db):
    org, _ = make_org(db, name="Inv Org 1")
    a, b = _two_accounts(db, org.id)
    # Debits 1000 vs credits 999 -> unbalanced
    with pytest.raises(UnbalancedJournalError):
        post_journal(
            db,
            org_id=org.id,
            currency="USD",
            lines=[
                JournalLine(account_id=a.id, debit_minor=1000),
                JournalLine(account_id=b.id, credit_minor=999),
            ],
        )


def test_single_line_rejected(db):
    org, _ = make_org(db, name="Inv Org 2")
    a, _ = _two_accounts(db, org.id)
    with pytest.raises(UnbalancedJournalError):
        post_journal(
            db,
            org_id=org.id,
            currency="USD",
            lines=[JournalLine(account_id=a.id, debit_minor=500)],
        )


def test_zero_amount_line_rejected(db):
    org, _ = make_org(db, name="Inv Org 3")
    a, b = _two_accounts(db, org.id)
    # Both lines zero-sided (0/0) -> each line must be exactly one-sided nonzero
    with pytest.raises(UnbalancedJournalError):
        post_journal(
            db,
            org_id=org.id,
            currency="USD",
            lines=[
                JournalLine(account_id=a.id, debit_minor=0, credit_minor=0),
                JournalLine(account_id=b.id, debit_minor=0, credit_minor=0),
            ],
        )
    # One zero line + one valid line still rejected (zero line not one-sided)
    with pytest.raises(UnbalancedJournalError):
        post_journal(
            db,
            org_id=org.id,
            currency="USD",
            lines=[
                JournalLine(account_id=a.id, debit_minor=1000),
                JournalLine(account_id=b.id, debit_minor=0, credit_minor=0),
            ],
        )


def test_double_sided_line_rejected(db):
    org, _ = make_org(db, name="Inv Org 4")
    a, b = _two_accounts(db, org.id)
    with pytest.raises(UnbalancedJournalError):
        post_journal(
            db,
            org_id=org.id,
            currency="USD",
            lines=[
                JournalLine(account_id=a.id, debit_minor=500, credit_minor=500),
                JournalLine(account_id=b.id, credit_minor=500),
            ],
        )


def test_negative_amounts_rejected(db):
    org, _ = make_org(db, name="Inv Org 5")
    a, b = _two_accounts(db, org.id)
    with pytest.raises(UnbalancedJournalError):
        post_journal(
            db,
            org_id=org.id,
            currency="USD",
            lines=[
                JournalLine(account_id=a.id, debit_minor=-100),
                JournalLine(account_id=b.id, credit_minor=100),
            ],
        )
    with pytest.raises(UnbalancedJournalError):
        post_journal(
            db,
            org_id=org.id,
            currency="USD",
            lines=[
                JournalLine(account_id=a.id, debit_minor=100),
                JournalLine(account_id=b.id, credit_minor=-100),
            ],
        )


def test_cross_org_account_rejected(db):
    org1, _ = make_org(db, name="Org A")
    org2, _ = make_org(db, name="Org B")
    a = make_account(db, org1.id, "1000", "Cash A", "asset", "USD")
    b = make_account(db, org2.id, "1000", "Cash B", "asset", "USD")
    with pytest.raises(LedgerError):
        post_journal(
            db,
            org_id=org1.id,
            currency="USD",
            lines=[
                JournalLine(account_id=a.id, debit_minor=1000),
                JournalLine(account_id=b.id, credit_minor=1000),
            ],
        )


def test_mixed_currency_journal_rejected(db):
    org, _ = make_org(db, name="Inv Org 6")
    usd_cash = make_account(db, org.id, "1000", "Cash USD", "asset", "USD")
    eur_cash = make_account(db, org.id, "1010", "Cash EUR", "asset", "EUR")
    with pytest.raises(LedgerError):
        post_journal(
            db,
            org_id=org.id,
            currency="USD",
            lines=[
                JournalLine(account_id=usd_cash.id, debit_minor=1000),
                JournalLine(account_id=eur_cash.id, credit_minor=1000),
            ],
        )


def test_locked_account_rejected(db):
    org, _ = make_org(db, name="Inv Org 7")
    a, b = _two_accounts(db, org.id)
    a.is_locked = 1
    db.flush()
    with pytest.raises(AccountLockedError):
        post_journal(
            db,
            org_id=org.id,
            currency="USD",
            lines=[
                JournalLine(account_id=a.id, debit_minor=1000),
                JournalLine(account_id=b.id, credit_minor=1000),
            ],
        )


def test_float_money_never_used(db):
    # USD has exponent 2; more than 2 decimals must be rejected, never rounded.
    with pytest.raises(MoneyError):
        parse_amount_to_minor("10.251", "USD")
    # JPY has exponent 0.
    assert parse_amount_to_minor("100", "JPY") == 100
    with pytest.raises(MoneyError):
        parse_amount_to_minor("100.5", "JPY")
    # Sanity: normal USD parsing still works with independently known values.
    assert parse_amount_to_minor("10.25", "USD") == 1025
    assert parse_amount_to_minor("0.01", "USD") == 1


def test_trial_balance_balanced_after_valid_postings(db):
    org, _ = make_org(db, name="Inv Org 8")
    cash = make_account(db, org.id, "1000", "Cash", "asset", "USD")
    equity = make_account(db, org.id, "3000", "Equity", "equity", "USD")
    post_journal(
        db,
        org_id=org.id,
        currency="USD",
        lines=[
            JournalLine(account_id=cash.id, debit_minor=5000),
            JournalLine(account_id=equity.id, credit_minor=5000),
        ],
    )
    post_journal(
        db,
        org_id=org.id,
        currency="USD",
        lines=[
            JournalLine(account_id=cash.id, debit_minor=2500),
            JournalLine(account_id=equity.id, credit_minor=2500),
        ],
    )
    tb = trial_balance(db, org_id=org.id)
    assert tb["balanced"] is True
    assert tb["total_debit"] == tb["total_credit"]
    assert tb["total_debit"] == 7500
    assert tb["total_credit"] == 7500
