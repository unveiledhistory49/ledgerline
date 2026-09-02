"""CLI: serve, seed, org provisioning, and report commands (argparse-only, no extra deps)."""

from __future__ import annotations

import argparse
import json
import os
import sys

from sqlalchemy import select

from ledgerline.auth import new_api_key
from ledgerline.db import create_app_engine, create_session_factory, init_db
from ledgerline.ledger import JournalLine, post_journal
from ledgerline.models import Account, Organization


def _session(db_url: str):  # type: ignore[no-untyped-def]
    engine = create_app_engine(db_url)
    init_db(engine)
    return create_session_factory(engine)()


def cmd_provision_org(args: argparse.Namespace) -> int:
    db = _session(args.db)
    try:
        raw, digest, prefix = new_api_key()
        org = Organization(name=args.name, api_key_hash=digest, key_prefix=prefix)
        db.add(org)
        db.commit()
        print(json.dumps({"org_id": org.id, "name": org.name, "api_key": raw}))
        return 0
    finally:
        db.close()


def cmd_seed(args: argparse.Namespace) -> int:
    """Seed a demo org with a full chart of accounts + opening balances."""
    db = _session(args.db)
    try:
        existing = db.execute(
            select(Organization).where(Organization.name == "Demo Co")
        ).scalar_one_or_none()
        if existing is None:
            raw, digest, prefix = new_api_key()
            org = Organization(name="Demo Co", api_key_hash=digest, key_prefix=prefix)
            db.add(org)
            db.flush()
            print(f"DEMO_API_KEY={raw}")
        else:
            org = existing
            print(f"DEMO_API_KEY=<already provisioned org={org.id}>")
        defs = [
            ("1000", "Cash", "asset", "USD"),
            ("1200", "AR", "asset", "USD"),
            ("2000", "AP", "liability", "USD"),
            ("3000", "Equity", "equity", "USD"),
            ("4000", "Revenue", "revenue", "USD"),
            ("5000", "Rent", "expense", "USD"),
        ]
        accs: dict[str, Account] = {}
        for code, name, typ, ccy in defs:
            a = db.execute(
                select(Account).where(Account.org_id == org.id, Account.code == code)
            ).scalar_one_or_none()
            if a is None:
                a = Account(org_id=org.id, code=code, name=name, type=typ, currency=ccy)
                db.add(a)
                db.flush()
            accs[code] = a
        # Opening equity: debit cash 100000 ($1000.00), credit equity.
        from ledgerline.models import Entry

        n = db.query(Entry).filter(Entry.org_id == org.id).count()
        if n == 0:
            post_journal(
                db,
                org_id=org.id,
                currency="USD",
                description="Opening balance",
                lines=[
                    JournalLine(account_id=accs["1000"].id, debit_minor=100000),
                    JournalLine(account_id=accs["3000"].id, credit_minor=100000),
                ],
            )
            post_journal(
                db,
                org_id=org.id,
                currency="USD",
                description="Sale",
                lines=[
                    JournalLine(account_id=accs["1000"].id, debit_minor=25000),
                    JournalLine(account_id=accs["4000"].id, credit_minor=25000),
                ],
            )
        db.commit()
        print(f"seeded org={org.id}")
        return 0
    finally:
        db.close()


def cmd_serve(args: argparse.Namespace) -> int:
    os.environ["LEDGERLINE_DATABASE_URL"] = args.db
    import uvicorn

    uvicorn.run("ledgerline.api:app", host=args.host, port=args.port, reload=False)
    return 0


def cmd_trial_balance(args: argparse.Namespace) -> int:
    from ledgerline import reports as r

    db = _session(args.db)
    try:
        org = db.execute(select(Organization)).scalars().first()
        if org is None:
            print("no organizations; run seed first", file=sys.stderr)
            return 1
        print(json.dumps(r.trial_balance(db, org_id=args.org_id or org.id), indent=2))
        return 0
    finally:
        db.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ledgerline")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("provision-org")
    s.add_argument("--name", required=True)
    s.add_argument(
        "--db", default=os.environ.get("LEDGERLINE_DATABASE_URL", "sqlite:///./ledgerline.db")
    )
    s.set_defaults(fn=cmd_provision_org)
    s2 = sub.add_parser("seed")
    s2.add_argument(
        "--db", default=os.environ.get("LEDGERLINE_DATABASE_URL", "sqlite:///./ledgerline.db")
    )
    s2.set_defaults(fn=cmd_seed)
    s3 = sub.add_parser("serve")
    s3.add_argument(
        "--db", default=os.environ.get("LEDGERLINE_DATABASE_URL", "sqlite:///./ledgerline.db")
    )
    s3.add_argument("--host", default="127.0.0.1")
    s3.add_argument("--port", type=int, default=8000)
    s3.set_defaults(fn=cmd_serve)
    s4 = sub.add_parser("trial-balance")
    s4.add_argument(
        "--db", default=os.environ.get("LEDGERLINE_DATABASE_URL", "sqlite:///./ledgerline.db")
    )
    s4.add_argument("--org-id", default=None)
    s4.set_defaults(fn=cmd_trial_balance)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    raise SystemExit(args.fn(args))


if __name__ == "__main__":
    main()
