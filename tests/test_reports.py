import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src"))
sys.path.insert(0, "/root/ledgerline/src")

from datetime import datetime

import pytest
from conftest import make_account, make_org

from ledgerline.ledger import JournalLine, PeriodClosedError, post_journal
from ledgerline.models import PeriodClose
from ledgerline.reports import balance_sheet, income_statement, trial_balance


def _setup_chart(db):
    org, _ = make_org(db, name="Reports Org")
    cash = make_account(db, org.id, "1000", "Cash", "asset", "USD")
    equity = make_account(db, org.id, "3000", "Opening Equity", "equity", "USD")
    sales = make_account(db, org.id, "4000", "Sales", "revenue", "USD")
    rent = make_account(db, org.id, "5000", "Rent", "expense", "USD")
    return org, cash, equity, sales, rent


def _post_fixture_transactions(db, org, cash, equity, sales, rent):
    # Opening equity: Cash Dr 100000 ($1000.00) / Equity Cr 100000
    post_journal(
        db,
        org_id=org.id,
        currency="USD",
        description="opening equity",
        lines=[
            JournalLine(account_id=cash.id, debit_minor=100000),
            JournalLine(account_id=equity.id, credit_minor=100000),
        ],
    )
    # Sale: Cash Dr 25000 ($250.00) / Sales Cr 25000
    post_journal(
        db,
        org_id=org.id,
        currency="USD",
        description="sale",
        lines=[
            JournalLine(account_id=cash.id, debit_minor=25000),
            JournalLine(account_id=sales.id, credit_minor=25000),
        ],
    )
    # Expense: Rent Dr 10000 ($100.00) / Cash Cr 10000
    post_journal(
        db,
        org_id=org.id,
        currency="USD",
        description="rent",
        lines=[
            JournalLine(account_id=rent.id, debit_minor=10000),
            JournalLine(account_id=cash.id, credit_minor=10000),
        ],
    )
    db.flush()


def test_trial_balance_totals_equal_and_balanced(db):
    org, cash, equity, sales, rent = _setup_chart(db)
    _post_fixture_transactions(db, org, cash, equity, sales, rent)
    tb = trial_balance(db, org_id=org.id)
    # Independently computed: total debits = 100000+25000+10000 = 135000
    # total credits = 100000+25000+10000 = 135000
    assert tb["balanced"] is True
    assert tb["total_debit"] == 135000
    assert tb["total_credit"] == 135000
    assert tb["total_debit"] == tb["total_credit"]
    by_code = {ln["code"]: ln for ln in tb["lines"]}
    # Cash: Dr 125000 Cr 10000 net 115000
    assert by_code["1000"]["debit"] == 125000
    assert by_code["1000"]["credit"] == 10000
    assert by_code["1000"]["net"] == 115000
    # Equity: Cr 100000 net -100000
    assert by_code["3000"]["debit"] == 0
    assert by_code["3000"]["credit"] == 100000
    assert by_code["3000"]["net"] == -100000
    # Sales: Cr 25000 net -25000
    assert by_code["4000"]["net"] == -25000
    # Rent: Dr 10000 net 10000
    assert by_code["5000"]["net"] == 10000


def test_balance_sheet_equation_holds(db):
    org, cash, equity, sales, rent = _setup_chart(db)
    _post_fixture_transactions(db, org, cash, equity, sales, rent)
    bs = balance_sheet(db, org_id=org.id)
    # Independently computed minor values:
    # assets = 115000, liabilities = 0, equity = 100000 + 25000 - 10000 = 115000
    assert bs["assets"] == 115000
    assert bs["liabilities"] == 0
    assert bs["equity"] == 115000
    assert bs["net_income"] == 15000
    assert bs["assets"] == bs["liabilities"] + bs["equity"]
    assert bs["balances"] is True


def test_income_statement_net(db):
    org, cash, equity, sales, rent = _setup_chart(db)
    _post_fixture_transactions(db, org, cash, equity, sales, rent)
    inc = income_statement(db, org_id=org.id)
    # Independently computed: revenue 25000, expenses 10000, net 15000
    assert inc["revenue"] == 25000
    assert inc["expenses"] == 10000
    assert inc["net_income"] == 15000
    assert inc["net_income"] == inc["revenue"] - inc["expenses"]


def test_period_close_blocks_backdated_posting(db):
    org, cash, equity, sales, rent = _setup_chart(db)
    _post_fixture_transactions(db, org, cash, equity, sales, rent)
    db.add(PeriodClose(org_id=org.id, period="2024-05", trial_balance_hash="abc"))
    db.flush()
    with pytest.raises(PeriodClosedError):
        post_journal(
            db,
            org_id=org.id,
            currency="USD",
            posted_at=datetime(2024, 5, 15, 12, 0, 0),
            lines=[
                JournalLine(account_id=cash.id, debit_minor=1000),
                JournalLine(account_id=equity.id, credit_minor=1000),
            ],
        )
    # Posting in a different (open) month still works.
    j = post_journal(
        db,
        org_id=org.id,
        currency="USD",
        posted_at=datetime(2024, 6, 15, 12, 0, 0),
        lines=[
            JournalLine(account_id=cash.id, debit_minor=1000),
            JournalLine(account_id=equity.id, credit_minor=1000),
        ],
    )
    assert j.id is not None
