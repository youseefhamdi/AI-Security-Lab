#!/usr/bin/env bash
set -euo pipefail

LIGHTRAG_URL="${LIGHTRAG_URL:-http://127.0.0.1:9621}"
MEM0_URL="${MEM0_URL:-http://127.0.0.1:8888}"
AURORA_URL="${AURORA_URL:-http://127.0.0.1:5000}"
KNOWLEDGE_URL="${KNOWLEDGE_URL:-http://127.0.0.1:5011}"
MEM0_API_KEY="${MEM0_API_KEY:-${MEM0_ADMIN_API_KEY:-mem0_lab_admin_key_change_me}}"
TIMEOUT="${FULL_PROFILE_TIMEOUT:-30}"

log() {
  printf '[verify-full] %s\n' "$*"
}

fail() {
  printf '[verify-full] ERROR: %s\n' "$*" >&2
  exit 1
}

if [[ "${RUNTIME:-0}" != "1" ]]; then
  log "Static/VPS mode: no full-profile requests will run"
  log "Local execution: RUNTIME=1 ./scripts/verify_full_profile.sh"
  exit 0
fi

command -v curl >/dev/null 2>&1 || fail "RUNTIME=1 requires curl"
command -v python3 >/dev/null 2>&1 || fail "RUNTIME=1 requires python3"

get_json() {
  local name="$1"
  local url="$2"
  local body
  log "Checking ${name}: ${url}" >&2
  body="$(curl --silent --show-error --fail --max-time "$TIMEOUT" --header 'Accept: application/json' "$url")" \
    || fail "${name} did not return a successful response"
  python3 -c 'import json, sys; json.loads(sys.argv[1])' "$body" \
    || fail "${name} returned invalid JSON"
  printf '%s' "$body"
}

health="$(get_json "LightRAG health" "${LIGHTRAG_URL%/}/health")"
python3 -c 'import json, sys; body=json.loads(sys.argv[1]); assert body.get("status") in (None, "healthy", "ok", "ready"), body' "$health" \
  || fail "LightRAG health reported an unhealthy status"

knowledge_health="$(get_json "Knowledge Agent health" "${KNOWLEDGE_URL%/}/health")"
python3 -c 'import json, sys; body=json.loads(sys.argv[1]); assert body.get("external_context") is True, body' "$knowledge_health" \
  || fail "Knowledge Agent external context is disabled"

log "Querying LightRAG"
lightrag_result="$(curl --silent --show-error --fail --max-time "$TIMEOUT" \
  --header 'Accept: application/json' \
  --header 'Content-Type: application/json' \
  --data '{"query":"What is the Zodiac Bank staff leave policy?","mode":"hybrid"}' \
  "${LIGHTRAG_URL%/}/query")" \
  || fail "LightRAG query failed"
python3 -c 'import json, sys; body=json.loads(sys.argv[1]); assert body, body' "$lightrag_result" \
  || fail "LightRAG returned an empty response"

log "Querying Mem0 with the configured API key"
mem0_result="$(curl --silent --show-error --fail --max-time "$TIMEOUT" \
  --header 'Accept: application/json' \
  --header 'Content-Type: application/json' \
  --header "X-API-Key: ${MEM0_API_KEY}" \
  --data '{"query":"What leave information is associated with ZB-CUS-001?","user_id":"ZB-CUS-001","run_id":"verify-full-profile"}' \
  "${MEM0_URL%/}/search")" \
  || fail "Mem0 search failed; check MEM0_API_KEY and the Mem0 provider configuration"
python3 -c 'import json, sys; body=json.loads(sys.argv[1]); assert body is not None, body' "$mem0_result" \
  || fail "Mem0 returned invalid search data"

log "Checking Aurora's end-to-end external-context response"
aurora_result="$(curl --silent --show-error --fail --max-time "$TIMEOUT" \
  --header 'Accept: application/json' \
  --header 'Content-Type: application/json' \
  --data '{"query":"What staff leave information is available?","session_id":"verify-full-profile","user_id":"ZB-CUS-001"}' \
  "${AURORA_URL%/}/api/chat")" \
  || fail "Aurora chat failed; check Bonsai, LightRAG, and Mem0 logs"
python3 -c '
import json, sys
body = json.loads(sys.argv[1])
assert body.get("rag") is not None, body
assert "memories" in body, body
print("Aurora external context fields present")
' "$aurora_result" 

log "Full-profile LightRAG + Mem0 verification passed"
