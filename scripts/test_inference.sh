#!/usr/bin/env bash
set -euo pipefail

BONSAI_ENDPOINT="${BONSAI_ENDPOINT:-http://127.0.0.1:11435/v1/chat/completions}"
BONSAI_MODEL="${BONSAI_MODEL:-bonsai-27b}"
REQUEST_TIMEOUT="${REQUEST_TIMEOUT:-300}"

log() {
  printf '[test-inference] %s\n' "$*"
}

fail() {
  printf '[test-inference] ERROR: %s\n' "$*" >&2
  exit 1
}

if [[ "${RUNTIME:-0}" != "1" ]]; then
  log "Static/VPS mode: RUNTIME is not 1; no network requests will run"
  log "Local execution: RUNTIME=1 ./scripts/test_inference.sh"
  exit 0
fi

command -v curl >/dev/null 2>&1 || fail "RUNTIME=1 requires curl"
payload="{\"model\":\"${BONSAI_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply: BACKEND_OK\"}],\"temperature\":0,\"max_tokens\":16,\"stream\":false}"
log "Testing Bonsai at ${BONSAI_ENDPOINT}"
response="$(curl --silent --show-error --fail --connect-timeout 5 --max-time "$REQUEST_TIMEOUT" \
  --header 'Accept: application/json' --header 'Content-Type: application/json' \
  --data "$payload" "$BONSAI_ENDPOINT")" || fail "Bonsai request failed"
printf '%s\n' "$response"
printf '%s' "$response" | grep -Eq '"choices"[[:space:]]*:' || fail "Response did not contain OpenAI choices"
printf '%s' "$response" | grep -Eq 'BACKEND_OK' || fail "Response did not echo BACKEND_OK"
log "Bonsai response shape and BACKEND_OK echo verified"
