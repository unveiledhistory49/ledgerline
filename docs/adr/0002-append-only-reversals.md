# ADR-0002: Append-Only Ledger; Reversal as Compensating Journal

Status: accepted

## Context

`ledgerline/models.py` states the rule at the top: "The ledger is
append-only: posted rows are never mutated." `Journal` rows carry
`status` (`JournalStatus`: `pending` / `posted` / `voided` / `reversed`),
`reversal_of_id`, `reversed_by_id`, and `idempotency_key`; `Entry` rows
carry `debit_minor` / `credit_minor` guarded by CHECK constraints
`ck_entries_one_side` (exactly one side nonzero) and `ck_entries_nonneg`.

There is no UPDATE path for posted `Entry` amounts or accounts anywhere in
`ledgerline.ledger`. The only state change to a posted `Journal` is the
reversal bookkeeping described below.

## Decision

Posted rows are never mutated. Corrections use `reverse_journal()`
(`ledgerline/ledger.py`), exposed as
`POST /v1/journals/{journal_id}/reverse`, which appends a **compensating
journal**:

1. Load the original (`Journal.id`, org-scoped); raise `LedgerError` if
   missing.
2. Refuse if `orig.reversed_by_id` is set (`AlreadyReversedError` → HTTP
   409 via `ledgerline/api.py`) — one reversal per journal.
3. Refuse unless `orig.status == POSTED` (`LedgerError` → HTTP 422).
4. Refuse if the current period is closed, or if `orig.posted_at`'s period
   is closed (`PeriodClosedError` → HTTP 409, via `is_period_closed()` /
   `_period_of()` `%Y-%m` against `PeriodClose`).
5. Read all `Entry` rows of the original, mirror each
   (`debit_minor ↔ credit_minor`, `memo=f"reversal of {e.id}"`), and call
   `post_journal()` in the original's currency with
   `reference=f"REV:{orig.reference}"[:120]`.
6. Link both sides: `rev.reversal_of_id = orig.id`,
   `orig.reversed_by_id = rev.id`, `orig.status = REVERSED`.

`GET /v1/journals/{journal_id}` exposes `reversal_of_id` /
`reversed_by_id` so clients can walk the chain.

## Void vs. reverse

`JournalStatus.VOIDED` exists in the enum but **no code path sets it**
(the only `voided` reference outside the enum is a comment in
`reports.trial_balance()` about excluding voided rows). There is no
`POST .../void` endpoint and `reverse_journal()` never produces `VOIDED`.
Void is therefore reserved / unused; the implemented correction mechanism
is reverse-only. A void (mark-excluded without a compensating journal)
would destroy the property that the entry stream alone explains every
balance movement; reversal preserves it.

## Consequences for reporting

`ledgerline.reports.trial_balance()` counts both statuses:

```python
POSTED_STATUSES = (JournalStatus.POSTED.value, JournalStatus.REVERSED.value)
```

A `REVERSED` journal's entries **still count** in the trial balance; the
net effect zeroes out only because the compensating journal's mirrored
entries are also counted. Consequences:

- Trial balance stays balanced by construction (`total_debit ==
  total_credit`); reversal cannot unbalance it.
- History is preserved: `GET /v1/journals` / `GET /v1/reports/trial-balance`
  show both the original and the reversal (linked by
  `reversal_of_id` / `reversed_by_id`).
- `balance_sheet()` and `income_statement()` inherit this automatically
  (both derive from `trial_balance()`).
- Cost: account turnover (`account_balances()` debit/credit sums, and the
  per-account `debit`/`credit` in trial balance) grows with corrections —
  gross, not net, activity is visible. Clients wanting "excluding reversed"
  views must filter on journal status themselves.
- Closed-period journals are immutable even to reversal (both endpoints of
  the check above), so `PeriodClose.trial_balance_hash` recorded by
  `POST /v1/periods/close` remains a faithful snapshot.
