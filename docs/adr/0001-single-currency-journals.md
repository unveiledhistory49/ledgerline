# ADR-0001: Single-Currency Journals; FX Is Preview + Paired Transfers

Status: accepted

## Context

`ledgerline.ledger.post_journal()` is the only write path for balances
(called by `POST /v1/journals` and `POST /v1/transfers` in
`ledgerline.routers.journals`, and by `reverse_journal()`).
Its signature takes exactly one currency per journal:

```python
post_journal(db, *, org_id, lines: list[JournalLine], currency: str, ...)
```

`JournalLine` (`ledgerline/ledger.py`) carries only integer minor units
(`debit_minor`, `credit_minor`, `memo`) — it has **no currency field**.
`Journal.currency` (`ledgerline/models.py`, `String(3)`) likewise stores one
code per journal, and each `Account` has one `Account.currency`.

Enforcement lives in `load_journal_accounts()`:

```python
if a.currency != currency:
    raise LedgerError(f"account {a.code} is {a.currency}, journal is {currency} "
                      "(no mixed-currency journals)")
```

plus org-ownership and `is_locked` (`AccountLockedError`) checks.
`validate_lines()` then requires each line to be exactly one-sided
(debit XOR credit, nonzero, non-negative integers within `MAX_MINOR`) and
`total_debit == total_credit`, followed by a defensive post-write re-read
(`SELECT sum(debit_minor), sum(credit_minor) WHERE journal_id = ...`).

FX lives outside posting: `ledgerline.fx.set_rate()` / `get_rate()` /
`preview_conversion()` backed by the `FxRate` table (rate stored as an exact
`String(40)` decimal), `ledgerline.money.convert_minor()` (integer in,
integer out, `ROUND_HALF_UP`), and `ledgerline.ledger.parse_rate()`.
The API surface is `POST /v1/fx-rates` (record a rate) and
`GET /v1/fx-convert` (preview only — returns `{"rate", "out_minor"}` and
writes nothing). A cross-currency economic movement is therefore two paired
single-currency journals (e.g. one `USD` `POST /v1/transfers`, one `EUR`
`POST /v1/transfers`), optionally sized with a `GET /v1/fx-convert` preview.

## Decision

Journals stay single-currency. FX conversion never posts; it only previews.
Cross-currency effects are represented as paired single-currency transfers
linked by convention via `Journal.reference` / `description` (free text,
truncated to 120/500 chars in `post_journal()`), not by a DB-level link.

## Alternatives considered

1. **Multi-currency lines with per-line currency** (e.g. `JournalLine`
   gains a `currency` field, journal stores a presentation currency plus an
   FX table per line). Rejected: it breaks the core invariant
   `validate_lines()` checks — integer `sum(debits) == sum(credits)` — by
   requiring rate-dependent conversion inside the atomic post path.
   The post-write balance check, the `ck_entries_one_side` /
   `ck_entries_nonneg` CHECK constraints on `Entry`, and
   `reports.trial_balance()` (which sums minor units per account with no FX
   logic) would all need rate lookups, rate-at-what-time semantics, and
   rounding-error tolerance. That turns a deterministic integer equality
   into an approximate-decimal one.
2. **Single journal with an embedded FX line** (gain/loss auto-line).
   Rejected for the same reason, plus it hides policy (which rate?
   `FxRate.effective_at` as-of posting time vs. explicit?) inside
   `post_journal()`, which is currently pure and rate-free.
3. **Client-side only FX (no `fx` module at all)**. Rejected: we still want
   a server-side, auditable rate record (`FxRate` rows) and a single
   rounding implementation (`convert_minor()` with `HALF_UP`, zero-rounding
   and `MAX_MINOR` guards) so previews are consistent.

## Consequences

- Positive: balance invariant stays a cheap integer comparison; trial
  balance, `account_balances()`, and period-close hashing need no FX code;
  audit is simple (every `Entry` is already in the account's own currency).
- Positive: rounding policy is isolated in `money.convert_minor()` /
  `parse_amount_to_minor()` (excess precision rejected, e.g. `$10.251` for
  USD, instead of silently rounding).
- Negative: no atomic cross-currency journal — a paired transfer can
  half-complete (two `POST /v1/transfers` calls, two idempotency keys).
  Callers needing atomicity must reconcile via `reference` and reverse
  (`POST /v1/journals/{id}/reverse`) on partial failure.
- Negative: reports never convert; `GET /v1/reports/trial-balance` groups
  per account currency with no totals across currencies. Multi-currency
  consolidation is a client concern.
