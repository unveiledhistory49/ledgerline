#!/usr/bin/env bash
# LedgerLine end-to-end smoke test against a throwaway SQLite DB.
# Uses `$PYTHON -m ledgerline.cli` (provision-org) for setup and curl for HTTP.
set -euo pipefail

PORT="${PORT:-8000}"
PYTHON="${PYTHON:-python3}"
BASE="http://127.0.0.1:${PORT}"
IDEMPOTENCY_KEY="e2e-smoke-key-001" # 8..64 chars per deps.validate_idempotency_key

TMPDB="$(mktemp /tmp/ledgerline-e2e-XXXXXX.db)"
rm -f "$TMPDB" # let SQLite create it fresh; keeps the mktemp-reserved path
DB_URL="sqlite:///${TMPDB}"
export LEDGERLINE_DATABASE_URL="$DB_URL"

SERVER_PID=""
cleanup() {
  if [ -n "$SERVER_PID" ]; then
    kill "$SERVER_PID" 2>/dev/null || true
  fi
  rm -f "$TMPDB"
}
trap cleanup EXIT

echo "==> provision org (db: ${DB_URL})"
PROVISION_JSON="$($PYTHON -m ledgerline.cli provision-org --name "e2e-org" --db "$DB_URL")"
echo "$PROVISION_JSON"
# jq-free parsing of the API key.
API_KEY="$($PYTHON -c 'import json,sys; print(json.loads(sys.argv[1])["api_key"])' "$PROVISION_JSON")"
if [ -z "$API_KEY" ]; then
  echo "FAIL: empty api key" >&2
  exit 1
fi

echo "==> start server on :${PORT}"
$PYTHON -m ledgerline.cli serve --db "$DB_URL" --host 127.0.0.1 --port "$PORT" &
SERVER_PID=$!

echo "==> wait for /health"
for i in $(seq 1 50); do
  if curl -fsS "$BASE/health" > /dev/null; then
    echo "server up"
    break
  fi
  if [ "$i" -eq 50 ]; then
    echo "FAIL: server did not become healthy" >&2
    exit 1
  fi
  sleep 0.2
done

echo "==> create 2 accounts"
A_JSON="$(curl -fsS -X POST "$BASE/v1/accounts" \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"code":"1000","name":"Cash","type":"asset","currency":"USD"}')"
B_JSON="$(curl -fsS -X POST "$BASE/v1/accounts" \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"code":"4000","name":"Revenue","type":"revenue","currency":"USD"}')"
A_ID="$($PYTHON -c 'import json,sys; print(json.loads(sys.argv[1])["id"])' "$A_JSON")"
B_ID="$($PYTHON -c 'import json,sys; print(json.loads(sys.argv[1])["id"])' "$B_JSON")"
echo "cash=$A_ID revenue=$B_ID"

TRANSFER_BODY="$($PYTHON -c 'import json,sys; print(json.dumps({"debit_account_id": sys.argv[1], "credit_account_id": sys.argv[2], "amount": "25.50", "currency": "USD", "description": "e2e smoke"}))' "$A_ID" "$B_ID")"

echo "==> POST transfer with Idempotency-Key"
T1="$(curl -fsS -X POST "$BASE/v1/transfers" \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -H "Idempotency-Key: $IDEMPOTENCY_KEY" \
  -d "$TRANSFER_BODY")"
echo "$T1"

echo "==> replay same Idempotency-Key, assert same journal id"
T2="$(curl -fsS -X POST "$BASE/v1/transfers" \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -H "Idempotency-Key: $IDEMPOTENCY_KEY" \
  -d "$TRANSFER_BODY")"
echo "$T2"
$PYTHON -c 'import json,sys; a=json.loads(sys.argv[1])["id"]; b=json.loads(sys.argv[2])["id"]; assert a == b, "replay mismatch"; print("idempotent replay ok:", a)' "$T1" "$T2"

echo "==> GET trial-balance, assert balanced==true"
TB="$(curl -fsS "$BASE/v1/reports/trial-balance" -H "X-API-Key: $API_KEY")"
$PYTHON -c 'import json,sys; d=json.loads(sys.argv[1]); assert d["balanced"] is True, d; print("trial-balance ok: debit=", d["total_debit"], "credit=", d["total_credit"])' "$TB"

echo "==> GET balance-sheet, assert balances==true"
BS="$(curl -fsS "$BASE/v1/reports/balance-sheet" -H "X-API-Key: $API_KEY")"
$PYTHON -c 'import json,sys; d=json.loads(sys.argv[1]); assert d["balances"] is True, d; print("balance-sheet ok: assets=", d["assets"], "liabilities=", d["liabilities"], "equity=", d["equity"])' "$BS"

echo "E2E PASS"
