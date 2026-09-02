import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src"))
sys.path.insert(0, "/root/ledgerline/src")

import pytest
from conftest import make_account, make_org
from sqlalchemy import select

from ledgerline.ledger import (
    AlreadyReversedError,
    JournalLine,
    LedgerError,
    account_balances,
    post_journal,
    reverse_journal,
)
from ledgerline.models import Entry, JournalStatus


def _setup_pair(db):
    org, _ = make_org(db, name="Reversal Org")
    cash = make_account(db, org.id, "1000", "Cash", "asset", "USD")
    equity = make_account(db, org.id, "3000", "Equity", "equity", "USD")
    return org, cash, equity


def test_reversal_creates_balanced_compensating_journal(db):
    org, cash, equity = _setup_pair(db)
    j = post_journal(
        db,
        org_id=org.id,
        currency="USD",
        lines=[
            JournalLine(account_id=cash.id, debit_minor=4200),
            JournalLine(account_id=equity.id, credit_minor=4200),
        ],
    )
    db.flush()
    orig_entries = db.execute(select(Entry).where(Entry.journal_id == j.id)).scalars().all()
    assert len(orig_entries) == 2

    rev = reverse_journal(db, org_id=org.id, journal_id=j.id)
    db.flush()
    rev_entries = db.execute(select(Entry).where(Entry.journal_id == rev.id)).scalars().all()
    assert len(rev_entries) == 2

    # Mirrored amounts: every original debit becomes a credit and vice versa.
    orig_by_acct = {e.account_id: (e.debit_minor, e.credit_minor) for e in orig_entries}
    rev_by_acct = {e.account_id: (e.debit_minor, e.credit_minor) for e in rev_entries}
    for acct_id, (od, oc) in orig_by_acct.items():
        rd, rc = rev_by_acct[acct_id]
        assert rd == oc
        assert rc == od

    # Reversal itself balanced.
    assert sum(e.debit_minor for e in rev_entries) == sum(e.credit_minor for e in rev_entries)
    assert sum(e.debit_minor for e in rev_entries) == 4200


def test_original_marked_reversed_with_link(db):
    org, cash, equity = _setup_pair(db)
    j = post_journal(
        db,
        org_id=org.id,
        currency="USD",
        lines=[
            JournalLine(account_id=cash.id, debit_minor=1000),
            JournalLine(account_id=equity.id, credit_minor=1000),
        ],
    )
    db.flush()
    rev = reverse_journal(db, org_id=org.id, journal_id=j.id)
    db.flush()
    db.refresh(j)
    db.refresh(rev)
    assert j.status == JournalStatus.REVERSED.value
    assert j.reversed_by_id == rev.id
    assert rev.reversal_of_id == j.id


def test_double_reversal_raises(db):
    org, cash, equity = _setup_pair(db)
    j = post_journal(
        db,
        org_id=org.id,
        currency="USD",
        lines=[
            JournalLine(account_id=cash.id, debit_minor=777),
            JournalLine(account_id=equity.id, credit_minor=777),
        ],
    )
    db.flush()
    reverse_journal(db, org_id=org.id, journal_id=j.id)
    db.flush()
    with pytest.raises(AlreadyReversedError):
        reverse_journal(db, org_id=org.id, journal_id=j.id)


def test_reversing_voided_or_nonexistent_raises(db):
    org, cash, equity = _setup_pair(db)
    j = post_journal(
        db,
        org_id=org.id,
        currency="USD",
        lines=[
            JournalLine(account_id=cash.id, debit_minor=500),
            JournalLine(account_id=equity.id, credit_minor=500),
        ],
    )
    db.flush()
    j.status = JournalStatus.VOIDED.value
    db.flush()
    with pytest.raises(LedgerError):
        reverse_journal(db, org_id=org.id, journal_id=j.id)
    with pytest.raises(LedgerError):
        reverse_journal(db, org_id=org.id, journal_id="00000000-0000-0000-0000-000000000000")


def test_net_effect_of_journal_plus_reversal_is_zero(db):
    org, cash, equity = _setup_pair(db)
    j = post_journal(
        db,
        org_id=org.id,
        currency="USD",
        lines=[
            JournalLine(account_id=cash.id, debit_minor=3333),
            JournalLine(account_id=equity.id, credit_minor=3333),
        ],
    )
    db.flush()
    reverse_journal(db, org_id=org.id, journal_id=j.id)
    db.flush()
    cash_bal = account_balances(db, org_id=org.id, account_id=cash.id)
    equity_bal = account_balances(db, org_id=org.id, account_id=equity.id)
    # Journal + exact mirror => net zero on both accounts.
    assert cash_bal["net"] == 0
    assert equity_bal["net"] == 0
    assert cash_bal["debit_turnover"] == 3333
    assert cash_bal["credit_turnover"] == 3333
    assert equity_bal["debit_turnover"] == 3333
    assert equity_bal["credit_turnover"] == 3333
