# LedgerLine

Double-entry accounting ledger with an append-only, idempotent HTTP API.

**What it is:** every movement of money is a balanced journal (debits == credits) persisted as immutable rows. Corrections are new reversal journals, never edits. Writes accept an `Idempotency-Key` so retries are safe.

**Who it's for:** developers embedding a small-business ledger, multi-org SaaS bookkeeping, or anyone who needs auditable trial-balance / balance-sheet / income-statement reports without running a full ERP.

## Architecture

```mermaid
flowchart TB
  Org[Organization] --> Acct[Accounts]
  Acct --> J[\"Journals (balanced, single-currency)\"]
  J --> E[\"Entries (debit_minor / credit_minor)\"]
  subgraph API[FastAPI]
    RA[\"routers/accounts\"]
    RJ[\"routers/journals\"]
    RR[\"routers/reports\"]
    AD[\"routers/admin (fx, periods, webhooks)\"]
  end
  RA --> L[ledger.py posting logic]
  RJ --> L
  RJ --> OB[\"webhook outbox\"]
  L --> DB[(SQLAlchemy DB)]
  OB -->|HMAC sha256=...| Ext[External HTTPS endpoint]
```

Auth is per-org API keys (`X-API-Key`, see `deps.py:require_org`). Reporting reads come from `reports.py`.

## 60-second quickstart

```bash
pip install -e ".[dev,test]"
python -m ledgerline.cli seed --db sqlite:///./ledgerline.db
python -m ledgerline.cli serve --db sqlite:///./ledgerline.db &
```

Provision an org (or reuse the `DEMO_API_KEY` printed by `seed`):

```bash
python -m ledgerline.cli provision-org --name "Acme"
# {"org_id": "<ORG_ID>", "name": "Acme", "api_key": "<KEY>"}
export KEY=<KEY>
```

Create accounts (field names from `schemas.py:AccountCreate`):

```bash
curl -s -X POST localhost:8000/v1/accounts -H "X-API-Key: $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"code":"1000","name":"Cash","type":"asset","currency":"USD"}'
curl -s -X POST localhost:8000/v1/accounts -H "X-API-Key: $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"code":"4000","name":"Revenue","type":"revenue","currency":"USD"}'
```

Transfer with an idempotency key (fields from `schemas.py:TransferCreate`):

```bash
curl -s -X POST localhost:8000/v1/transfers \
  -H "X-API-Key: $KEY" -H "Idempotency-Key: acme-sale-001" \
  -H 'Content-Type: application/json' \
  -d '{"debit_account_id":"<CASH_ID>","credit_account_id":"<REV_ID>",
       "amount":"25.00","currency":"USD","description":"Sale","reference":"inv-1"}'
```

Check the trial balance:

```bash
curl -s localhost:8000/v1/reports/trial-balance -H "X-API-Key: $KEY"
python -m ledgerline.cli trial-balance --org-id <ORG_ID>
```

## Design decisions

- **Integer minor units, never float.** API takes decimal strings (`"25.00"`); `money.py` converts to `int` minor units with currency exponents (JPY/KRW 0, most 2, KWD/BHD 3) and a `MAX_MINOR = 10**15` overflow guard.
- **Single-currency journals.** `ledger.py` rejects mixed-currency lines; cross-currency reporting uses the `fx` table (`POST /v1/fx-rates`).
- **Append-only + reversals.** Posted journals/entries are never mutated. `POST /v1/journals/{id}/reverse` posts a mirrored journal; double-reversal returns 409 (`AlreadyReversedError`).
- **Idempotency with payload-hash 409.** Same key + same body replays the stored response; same key + different body returns `409` (`deps.py:idem_replay_check`). Keys must be 8–64 chars.
- **Period close.** `POST /v1/periods/close` with `{"period":"2026-09"}` snapshots the trial-balance hash; posting into a closed month raises 409 (`PeriodClosedError`). No re-open endpoint.
- **Webhook outbox + HMAC.** `journal.posted` / `journal.reversed` events are queued per endpoint and delivered with `sha256=` HMAC (`webhooks.py:sign_payload`), with backoff retries; pump with `POST /v1/webhooks/dispatch`.

Config is env-driven with the `LEDGERLINE_` prefix (`config.py`): `LEDGERLINE_DATABASE_URL`, `LEDGERLINE_API_KEY_PEPPER`, `LEDGERLINE_WEBHOOK_TIMEOUT_SECONDS`, `LEDGERLINE_WEBHOOK_MAX_ATTEMPTS`, `LEDGERLINE_DEFAULT_PAGE_SIZE`, `LEDGERLINE_MAX_PAGE_SIZE`.

## API

All org-scoped routes require `X-API-Key`. Errors: 401 bad key, 404 wrong-org/missing, 409 conflict (dup code, idempotency mismatch, double reverse, closed period), 422 validation/unbalanced.

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/health` | liveness, no auth |
| POST | `/v1/accounts` | `code, name, type, currency` |
| GET | `/v1/accounts?limit=&offset=` | ordered by code |
| GET | `/v1/accounts/{account_id}/balance` | debit/credit/net |
| POST | `/v1/journals` | `currency, description, reference, lines[{account_id, debit, credit, memo}]`; optional `Idempotency-Key` |
| GET | `/v1/journals?limit=&offset=` | newest first |
| GET | `/v1/journals/{journal_id}` | journal + entries |
| POST | `/v1/journals/{journal_id}/reverse` | append-only reversal |
| POST | `/v1/transfers` | `debit_account_id, credit_account_id, amount, currency, description, reference`; optional `Idempotency-Key` |
| GET | `/v1/reports/trial-balance` | `{lines, total_debit, total_credit, balanced, hash}` |
| GET | `/v1/reports/balance-sheet` | assets/liabs/equity |
| GET | `/v1/reports/income-statement` | revenue/expenses |
| POST | `/v1/fx-rates` | `base, quote, rate` |
| GET | `/v1/fx-convert?amount_minor=&base=&quote=` | preview conversion |
| POST | `/v1/periods/close` | `period: YYYY-MM` |
| POST | `/v1/webhook-endpoints` | `url` (https or http://localhost), `secret` (16–128 chars) |
| POST | `/v1/webhooks/dispatch` | deliver due outbox items |

CLI (`python -m ledgerline.cli <cmd>`): `provision-org --name`, `seed`, `serve [--host --port]`, `trial-balance [--org-id]`. All take `--db` (default `LEDGERLINE_DATABASE_URL` or `sqlite:///./ledgerline.db`).

## Testing / quality

```bash
make test       # python -m pytest tests/ -q
make lint       # ruff check + ruff format --check on src tests scripts
make typecheck  # mypy src/ledgerline (strict = true)
make fmt        # ruff format + --fix
make e2e        # bash scripts/e2e.sh
```

## Project layout

```
src/ledgerline/
  __init__.py
  api.py          # app factory, /health, error mapping
  auth.py         # API-key hashing / verification
  cli.py          # provision-org, seed, serve, trial-balance
  config.py       # LEDGERLINE_* settings
  db.py           # engine / session factory
  deps.py         # require_org (X-API-Key), idempotency helpers
  fx.py           # rates + conversion
  ledger.py       # posting invariants, balances, reversals
  models.py       # Organization, Account, Journal, Entry, outbox tables
  money.py        # minor-unit parsing, currency exponents
  reports.py      # trial-balance / balance-sheet / income-statement
  schemas.py      # Pydantic request shapes
  webhooks.py     # outbox enqueue, HMAC sign, dispatch
  routers/
    __init__.py
    accounts.py
    admin.py
    journals.py
    reports.py
```

## Limitations

- **SQLite default** (`sqlite:///./ledgerline.db`); Postgres works via `LEDGERLINE_DATABASE_URL` but migrations are create-all only.
- **Single-node.** No distributed locking; concurrent writers rely on DB transactions.
- **No real authZ** beyond per-org API keys (shared pepper hash, no roles, no key rotation endpoint).
- **Overdrafts permitted.** No balance/funds check on posting (no `InsufficientFundsError` raised in the write path); credits can exceed debits per account.
