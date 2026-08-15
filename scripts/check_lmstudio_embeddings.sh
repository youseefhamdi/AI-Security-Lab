#!/usr/bin/env bash
set -euo pipefail

LMSTUDIO_URL="${LMSTUDIO_EMBEDDING_URL:-http://127.0.0.1:1234/v1}"
MODEL="${LMSTUDIO_EMBEDDING_MODEL:-}"
EXPECTED_DIM="${EMBEDDING_DIM:-0}"
TIMEOUT="${EMBEDDING_TIMEOUT:-15}"

log() {
  printf '[lmstudio-embeddings] %s\n' "$*"
}

fail() {
  printf '[lmstudio-embeddings] ERROR: %s\n' "$*" >&2
  exit 1
}

if [[ "${RUNTIME:-0}" != "1" ]]; then
  log "Static/VPS mode: no LM Studio requests will run"
  log "Local execution: RUNTIME=1 LMSTUDIO_EMBEDDING_MODEL=... ./scripts/check_lmstudio_embeddings.sh"
  exit 0
fi

command -v curl >/dev/null 2>&1 || fail "RUNTIME=1 requires curl"
command -v python3 >/dev/null 2>&1 || fail "RUNTIME=1 requires python3"

models="$(curl --silent --show-error --fail --max-time "$TIMEOUT" \
  --header 'Accept: application/json' "${LMSTUDIO_URL%/}/models")" \
  || fail "LM Studio is not reachable at ${LMSTUDIO_URL}"

[[ -n "$MODEL" ]] || fail "Set LMSTUDIO_EMBEDDING_MODEL to the exact embedding model loaded in LM Studio; Bonsai is a text-generation model and is not sufficient"

python3 -c '
import json, sys
body = json.loads(sys.argv[1])
model = sys.argv[2]
ids = {str(row.get("id")) for row in body.get("data", []) if isinstance(row, dict)}
assert model in ids, {"requested": model, "available": sorted(ids)}
' "$models" "$MODEL" \
  || fail "LM Studio does not expose ${MODEL}; load that embedding model in LM Studio first"

log "Probing /embeddings for ${MODEL}"
embedding_response="$(curl --silent --show-error --fail --max-time "$TIMEOUT" \
  --header 'Accept: application/json' \
  --header 'Content-Type: application/json' \
  --data "{\"model\":\"${MODEL}\",\"input\":\"ZODIAC embedding probe\"}" \
  "${LMSTUDIO_URL%/}/embeddings")" \
  || fail "LM Studio rejected the embedding request; the loaded model may be generation-only"

python3 -c '
import json, sys
body = json.loads(sys.argv[1])
rows = body.get("data") or []
vector = (rows[0] if rows else {}).get("embedding")
assert isinstance(vector, list) and vector, body
expected = int(sys.argv[2])
if expected:
    assert len(vector) == expected, {"actual_dimension": len(vector), "expected_dimension": expected}
print(f"embedding probe passed; dimension={len(vector)}")
' "$embedding_response" "$EXPECTED_DIM" \
  || fail "LM Studio returned an invalid embedding vector or dimension"

log "LM Studio is ready for local embedding use"
