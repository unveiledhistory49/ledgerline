# LedgerLine Threat Model

## Assets

- **Ledger integrity**: `Journal` / `Entry` rows (SQLite, via
  `ledgerline.db.create_app_engine` / `settings.database_url`). Corruption
  or unauthorized writes destroy the core product guarantee (balanced,
  append-only books).
- **API keys**: bearer credentials for `X-API-Key` auth. `Organization`
  stores only `api_key_hash` (unique) + `key_prefix`; the raw key
  (`ll_live_<32 hex>` from `ledgerline.auth.new_api_key()`) is shown once
  at `provision-org` time.
- **Webhook secrets / payloads**: per-endpoint `WebhookEndpoint.secret`
  (HMAC signing) and `WebhookDelivery.payload` contents.
- **Availability / correctness of reports**: `trial_balance`,
  `balance_sheet`, `income_statement` (`ledgerline/reports.py`) computed
  live from entries.

## Trust boundaries

1. Public internet → FastAPI app (`ledgerline/api.py` + `routers/*`).
   Authenticated by `X-API-Key` via `deps.require_org()` →
   `auth.authenticate()`; unauthenticated only at `GET /health`.
2. App → SQLite file (`LEDGERLINE_DATABASE_URL`, default
   `sqlite:///./ledgerline.db`, WAL + `foreign_keys=ON` pragmas in
   `db.py`). Anyone with file/host access bypasses all app controls.
3. App → webhook receivers (`webhooks.dispatch_due()` via `httpx.post`,
   `X-LedgerLine-Signature: sha256=<hmac>` from `sign_payload()`).
4. Operator / CLI (`ledgerline/cli.py`: `provision-org`, `seed`, `serve`,
   `trial-balance`) → DB directly, bypassing HTTP auth.

## Threats and mitigations

- **Double-spend via retry** (client retries a `POST /v1/journals` or
  `POST /v1/transfers` after a timeout and posts twice).
  Mitigated by opt-in idempotency: `Idempotency-Key` (8..64 chars,
  `deps.validate_idempotency_key()`), stored as `IdempotencyRecord`
  (`org_id, key` unique) with `request_hash` / `response_status` /
  `response_body` / `journal_id` (`deps.idem_store()`); exact-payload
  replays return the original status code without re-posting, and
  same-key-different-payload returns 409 (`deps.idem_replay_check()`).
  Residual: requests sent *without* a key have no protection; clients must
  opt in.
- **API key leakage / theft** (logs, repo, backups).
  Mitigated: only `sha256(api_key_pepper + "::" + raw)` is stored
  (`auth.hash_key()`; pepper from `settings.api_key_pepper` /
  `LEDGERLINE_API_KEY_PEPPER`); `authenticate()` compares with
  `hmac.compare_digest()`; only the last 8 chars (`key_prefix`) are
  stored for support identification, so logs can use the prefix, never the
  full key. Residual: pepper defaults to `"dev-pepper-change-me"` and a
  leaked pepper + DB dump enables offline brute force of the 128-bit key
  space (infeasible but not nothing); full-DB read = full impersonation of
  every org (hashes are bearer-equivalent for DB readers — they can be
  swapped, not reversed). There is **no key rotation endpoint**; rotation
  is a manual DB update (see `docs/RUNBOOK.md`).
- **Webhook SSRF** (malicious `url` makes the server POST internal hosts).
  Mitigated: `POST /v1/webhook-endpoints` (`routers/admin.py`) rejects
  anything not starting with `https://` or `http://localhost` (422).
  Residual: `http://localhost` (any port) is still server-side fetch to
  loopback — expected for dev, but a compromised org credential can probe
  local services; no egress allowlist, no response-size cap, and
  deliveries carry journal/org IDs an internal listener might trust.
- **Mass assignment** (client sets privileged fields like `status`,
  `reversed_by_id`, `org_id`).
  Mitigated: writes go through narrow Pydantic schemas (`schemas.py`:
  `AccountCreate`, `JournalCreate`/`JournalLineIn`, `TransferCreate`,
  `FxRateCreate`, `WebhookEndpointCreate`, `PeriodCloseCreate`) and the
  `post_journal()` / `reverse_journal()` constructors, which set
  org/status/links server-side. Residual: defense relies on every router
  staying on schemas + `require_org()` scoping; a future endpoint that
  binds a model directly would bypass it.
- **Period-close bypass** (backdate into, or rewrite, a closed period).
  Mitigated: `is_period_closed()` (`_period_of()` `%Y-%m` vs
  `PeriodClose`) is enforced in `post_journal()` (by `posted_at`) and
  twice in `reverse_journal()` (current period *and* original's period);
  `POST /v1/periods/close` refuses to close an unbalanced ledger and
  refuses duplicates (409), recording `trial_balance_hash`.
  Residual: close is per-`YYYY-MM` string with no hierarchy (closing June
  does not lock May); `PeriodClose` rows can be deleted directly in the DB
  by anyone with file access; there is no reopen workflow.
- **Float rounding theft** (fractional-cent skimming via floats).
  Mitigated: all amounts are integers in minor units end-to-end
  (`money.parse_amount_to_minor()` from decimal strings only, excess
  precision *rejected* e.g. `$10.251`/USD, `convert_minor()` with
  `ROUND_HALF_UP`, `MAX_MINOR = 10**15` overflow guards, `validate_lines()`
  integer balance check + post-write re-read, `Entry` CHECK constraints
  `ck_entries_one_side`/`ck_entries_nonneg`). Residual: FX preview
  rounding (`convert_minor` HALF_UP to target minor) can still lose/gain a
  unit per conversion; paired cross-currency transfers are non-atomic, so
  an operator can observe or act on a half-completed pair.
- **Unauthenticated `GET /health`** (returns `{"ok", "version", "time"}`).
  Intentional: liveness probe for load balancers / containers carries no
  sensitive data. All `/v1/*` routes require `X-API-Key` (401 otherwise).

## Residual risks (stated honestly)

- SQLite file access = total compromise (read keys/hashes/secrets,
  rewrite history, delete `PeriodClose` / `IdempotencyRecord` rows).
  Backups inherit this sensitivity; see runbook.
- No rate limiting, audit log, per-key scopes, key rotation/expiry, or
  webhook payload signing-key rotation. `authenticate()` scans all org
  rows per request (fine at small scale, no lockout/backoff on guessing).
- Webhook retry is unbounded in time up to `webhook_max_attempts` (8,
  `RETRY_DELAYS` up to 24h) with plaintext payloads at rest in
  `WebhookDelivery`; `POST /v1/webhooks/dispatch` lets any valid key for
  the org trigger deliveries.
- `Account.is_locked` blocks posting (`AccountLockedError`) but there is
  no API to set it — locking is DB-only, so it cannot be relied on as an
  operational control yet.
