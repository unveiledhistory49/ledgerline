# ADR-0003: Idempotency via Key + Payload Hash (Same Key, Different Payload → 409)

Status: accepted

## Context

`POST /v1/journals` and `POST /v1/transfers`
(`ledgerline/routers/journals.py`) accept an optional `Idempotency-Key`
header. Without it, client retries (timeouts, 5xx, network partitions)
would double-post journals — a double-spend in ledger terms. Two designs
were on the table: (a) replay the original response whenever the key
matches, regardless of body; (b) replay only when the body matches, and
fail loudly otherwise.

## Decision

(b). Key match **plus** payload-hash match replays; key match with a
different payload returns **409 Conflict** instead of replaying.

Mechanics, all in `ledgerline/deps.py` (`validate_idempotency_key`,
`idem_lookup`, `idem_replay_check`, `idem_store`) and the two router
handlers:

- `validate_idempotency_key()`: if the header is present it must be
  **8..64 chars**, else HTTP 422. (`Journal.idempotency_key` is
  `String(64)` nullable; `IdempotencyRecord.key` is part of the composite
  primary key with a `uq_idem_org_key` unique constraint on
  `(org_id, key)` — keys are per-org, not global.)
- Canonicalization: `raw = json.dumps(body.model_dump(), sort_keys=True).encode()`.
- Lookup: `idem_lookup(db, org.id, key)` → `IdempotencyRecord | None`.
- Replay check: `idem_replay_check(raw, rec)` compares
  `hashlib.sha256(raw).hexdigest()` against stored `rec.request_hash`;
  mismatch raises HTTP **409** (`"Idempotency-Key already used with
  different payload"`).
- Replay: return `JSONResponse(status_code=rec.response_status,
  content=json.loads(rec.response_body))` — i.e. the **original status
  code** (201 for journal/transfer creation) and body, without calling
  `post_journal()` again. No new `journal.posted` webhook is enqueued.
- Store (only when a key was supplied and the post succeeded): `idem_store()`
  persists `method` (`"POST"`), `path` (`"/v1/journals"` or
  `"/v1/transfers"`), `request_hash` (sha256 hex), `response_status`,
  `response_body` (JSON string), and `journal_id`. `post_journal()` also
  stamps `Journal.idempotency_key` with the same key.

## Alternatives considered

- **Same key → always replay (ignore payload).** Rejected: a client bug
  that reuses a key for a *different* transfer (wrong amount, wrong
  account) would silently receive the old journal as if it had posted the
  new one — funds move differently than the caller believes, with a 201
  that looks like success. 409 surfaces the bug immediately.
- **Same key + different payload → post a second journal.** Rejected: this
  is exactly the double-spend the mechanism exists to prevent; "retry with
  edits" must use a new key.
- **No idempotency (client-generated journal UUIDs only).** Rejected:
  pushes dedup complexity to every client and still leaves the retry window
  open.

## Consequences

- Safe to retry `POST /v1/journals` / `POST /v1/transfers` with the same
  key + same body; the second call returns the stored 201 + body without
  touching the ledger.
- Key reuse across different payloads fails fast with 409 — callers must
  generate a fresh key per distinct operation.
- Keys are scoped to `(org_id, key)`; two orgs may use the same key
  independently.
- Requests without a key are never deduped; safety is opt-in per request.
- `IdempotencyRecord` rows (including full `response_body`) are retained
  indefinitely — storage grows with keyed writes; there is no TTL/GC.
