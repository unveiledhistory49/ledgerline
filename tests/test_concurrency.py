import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src"))
sys.path.insert(0, "/root/ledgerline/src")

from concurrent.futures import ThreadPoolExecutor

from conftest import make_account, make_org
from sqlalchemy import func, select

from ledgerline.ledger import JournalLine, post_journal
from ledgerline.models import Journal
from ledgerline.reports import trial_balance

N_THREADS = 16
TRANSFERS_PER_THREAD = 20
AMOUNT_MINOR = 100  # $1.00


def test_concurrent_transfers_balanced(tmp_path):
    from ledgerline.db import create_app_engine, create_session_factory, init_db

    db_path = tmp_path / "concurrency.db"
    url = f"sqlite:///{db_path}"
    engine = create_app_engine(url)
    init_db(engine)
    SessionFactory = create_session_factory(engine)

    # Setup org + two accounts in the main thread.
    setup = SessionFactory()
    org, _ = make_org(setup, name="Concurrency Org")
    org_id = org.id
    acc_a = make_account(setup, org_id, "1000", "Cash A", "asset", "USD")
    acc_b = make_account(setup, org_id, "1010", "Cash B", "asset", "USD")
    acc_a_id = acc_a.id
    acc_b_id = acc_b.id
    setup.commit()
    setup.close()

    def worker(_: int):
        for _i in range(TRANSFERS_PER_THREAD):
            s = SessionFactory()
            try:
                post_journal(
                    s,
                    org_id=org_id,
                    currency="USD",
                    lines=[
                        JournalLine(account_id=acc_a_id, debit_minor=AMOUNT_MINOR),
                        JournalLine(account_id=acc_b_id, credit_minor=AMOUNT_MINOR),
                    ],
                )
                s.commit()
            finally:
                s.close()

    with ThreadPoolExecutor(max_workers=N_THREADS) as pool:
        list(pool.map(worker, range(N_THREADS)))

    check = SessionFactory()
    try:
        n_journals = check.execute(
            select(func.count()).select_from(Journal).where(Journal.org_id == org_id)
        ).scalar_one()
        assert n_journals == N_THREADS * TRANSFERS_PER_THREAD == 320

        tb = trial_balance(check, org_id=org_id)
        assert tb["balanced"] is True
        assert tb["total_debit"] == tb["total_credit"]
        # Each journal contributes 100 debit + 100 credit by hand:
        # 320 * 100 = 32000 each side.
        assert tb["total_debit"] == 32000
        assert tb["total_credit"] == 32000
    finally:
        check.close()
        engine.dispose()
