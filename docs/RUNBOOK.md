# LedgerLine Runbook

## Provision an org

```bash
ledgerline provision-org --name "Acme Inc" --db sqlite:///./ledgerline.db
# {"org_id": "<uuid>", "name": "Acme Inc", "api_key": "ll_live_<32 hex>"}
```

Implemented by `cli.cmd_provision_org()` → `auth.new_api_key()` (stores
`sha256` hash + 8-char `key_prefix` on `Organization`, prints raw key
**once**). Save the `api_key` immediately — it cannot be recovered, only
replaced (see rotation). All `/v1/*` calls then use `X-API-Key: <key>`.
`ledgerline seed` provisions a `Demo Co` org with a USD chart of accounts
plus opening-balance journals for local dev.

## Rotate a key — NO rotation endpoint

There is **no** `POST /v1/.../rotate` or similar endpoint (verified: key
material is only touched by `cli.cmd_provision_org` / `cmd_seed` and
`auth.new_api_key()` / `hash_key()`). To rotate, update the DB directly:

```python
from ledgerline.auth import new_api_key
from ledgerline.models import Organization
raw, digest, prefix = new_api_key()
org = db.get(Organization, "<org_id>")
org.api_key_hash = digest
org.key_prefix = prefix
db.commit()
print(raw)  # deliver once, securely; old key stops working on commit
```

Generate with `auth.new_api_key()` (never invent key format by hand —
`authenticate()` requires the `ll_live_` prefix). Keep
`LEDGERLINE_API_KEY_PEPPER` stable: changing the pepper invalidates every
stored hash at once.

## Close a period

```bash
curl -X POST /v1/periods/close \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"period":"2026-08"}'
# {"period":"2026-08","hash":"<trial_balance_hash>"}
```

Handler: `routers/admin.py close_period()` (`PeriodCloseCreate.period`
must match `YYYY-MM`). It 409s if already closed and 422s if
`reports.trial_balance()` is unbalanced. Effects: `post_journal()` and
`reverse_journal()` raise `PeriodClosedError` (HTTP 409) for any
`posted_at` in that `YYYY-MM`, and `reverse_journal()` additionally
refuses to reverse journals originally posted in a closed period. Closes
are per-month strings (closing one month does not lock earlier open
ones); there is no reopen — reopening is a manual `DELETE FROM
period_closes WHERE org_id=... AND period=...`.

## Replay webhooks

```bash
curl -X POST /v1/webhooks/dispatch -H "X-API-Key: $KEY"
# {"delivered": N, "pending_or_dead": M}
```

Runs `webhooks.dispatch_due(db, limit=25)`: POSTs due `pending` rows
(`next_retry_at <= now`, canonical JSON body, headers
`X-LedgerLine-Event` / `X-LedgerLine-Signature: sha256=<hmac>` from
`sign_payload()`), marks 2xx as `delivered`, otherwise backs off per
`RETRY_DELAYS` (`[60s … 86400s]`) until `settings.webhook_max_attempts`
(8), then `dead`. Register receivers first via
`POST /v1/webhook-endpoints` (https-only, `http://localhost` allowed for
dev). Operate it on a cron/loop — nothing dispatches automatically.

## Back up the SQLite file

The DB is the file in `settings.database_url` (`LEDGERLINE_DATABASE_URL`,
default `sqlite:///./ledgerline.db`; `db.create_app_engine()` enables WAL
+ `foreign_keys=ON`). Back up with a consistent snapshot, not a bare `cp`
of a live WAL DB:

```bash
sqlite3 ledgerline.db ".backup 'ledgerline-$(date +%F).db'"
```

(`.backup` handles the `-wal`/`-shm` sidecars.) Protect the backup like
production secrets: it contains `api_key_hash`es (impersonation-capable on
restore/swap), webhook secrets, and full ledger history. Restore =
replace the file and restart (`init_db()` creates tables if missing).

## Trial balance unbalanced — should be impossible

`GET /v1/reports/trial-balance` (`reports.trial_balance()`) sums posted
(`POSTED` + `REVERSED`) entries; `validate_lines()` + the post-write
balance re-read in `post_journal()` plus `ck_entries_one_side` make an
unbalanced journal unpostable. If `balanced: false` ever appears, suspect
direct DB edits or application bugs, and find the culprit journal (one
whose entries do not net to zero):

```sql
SELECT journal_id,
       SUM(debit_minor)  AS d,
       SUM(credit_minor) AS c
FROM entries
WHERE org_id = '<org_id>'
GROUP BY journal_id
HAVING SUM(debit_minor) != SUM(credit_minor) OR SUM(debit_minor) = 0;
```

Then inspect with `GET /v1/journals/{journal_id}`, compare against
`POST /v1/periods/close`-recorded `trial_balance_hash`es in
`period_closes` to bound when divergence started, and stop writers until
resolved (freeze via file permissions / stop `ledgerline serve` — there is
no maintenance-mode flag). `ledgerline trial-balance --org-id <org_id>`
(`cli.cmd_trial_balance`) gives the same data offline.

## Headers and logs

- `X-API-Key: ll_live_...` — required on every `/v1/*` (else 401 from
  `deps.require_org()`); never log the full value, only `key_prefix`.
- `Idempotency-Key: <8..64 chars>` — optional on `POST /v1/journals` and
  `POST /v1/transfers`; log it to correlate retries (same key + same body
  = replay with original status; same key + different body = 409).
- `x-request-id` → echoed back as `X-Request-ID` with `X-Elapsed-Ms` by
  `api.create_app()`'s `request_id_mw` middleware (generates a UUID when
  absent). Include your `x-request-id` on every mutating call and have
  clients log the returned `X-Request-ID` — that is the join key between
  client logs, server logs, `Journal.id`, and `IdempotencyRecord`.
- `GET /health` is unauthenticated by design; exclude it from auth
  alerting, but alert on 5xx from any `/v1/*` and on spikes of 409s
  (idempotency conflicts, duplicate closes, `AlreadyReversedError`) and
  422s (unbalanced journals, bad amounts).
