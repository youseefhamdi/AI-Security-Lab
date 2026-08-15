#!/usr/bin/env bash
set -euo pipefail

LLAMA_ENDPOINT="${LLAMA_ENDPOINT:-http://127.0.0.1:11434/v1/chat/completions}"
BONSAI_ENDPOINT="${BONSAI_ENDPOINT:-http://127.0.0.1:11435/v1/chat/completions}"
LLAMA_MODEL="${LLAMA_MODEL:-llama3.2:1b}"
BONSAI_MODEL="${BONSAI_MODEL:-bonsai-27b}"
REQUEST_TIMEOUT="${REQUEST_TIMEOUT:-300}"

log() {
  printf '[test-inference] %s\n' "$*"
}

fail() {
  printf '[test-inference] ERROR: %s\n' "$*" >&2
  exit 1
}

run_backend_test() {
  local label="$1"
  local endpoint="$2"
  local model="$3"
  local payload
  local response

  payload="{\"model\":\"${model}\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply: BACKEND_OK\"}],\"temperature\":0,\"max_tokens\":16,\"stream\":false}"
  log "Testing ${label} at ${endpoint}"
  response="$(curl \
    --silent \
    --show-error \
    --fail \
    --connect-timeout 5 \
    --max-time "$REQUEST_TIMEOUT" \
    --header 'Accept: application/json' \
    --header 'Content-Type: application/json' \
    --data "$payload" \
    "$endpoint")" \
    || fail "${label} request failed"

  printf '%s\n' "$response"
  printf '%s' "$response" | grep -Eq '"choices"[[:space:]]*:' \
    || fail "${label} response did not contain OpenAI choices"
  printf '%s' "$response" | grep -Eq 'BACKEND_OK' \
    || fail "${label} response did not echo BACKEND_OK"
  log "${label}: response shape and BACKEND_OK echo verified"
}

if [[ "${RUNTIME:-0}" != "1" ]]; then
  log "Static/VPS mode: RUNTIME is not 1; no network requests will run"
  log "Local execution: RUNTIME=1 ./scripts/test_inference.sh"
  exit 0
fi

command -v curl >/dev/null 2>&1 || fail "RUNTIME=1 requires curl"
run_backend_test "llama3.2:1b" "$LLAMA_ENDPOINT" "$LLAMA_MODEL"
run_backend_test "bonsai-27b" "$BONSAI_ENDPOINT" "$BONSAI_MODEL"
log "Both OpenAI-compatible inference backends passed"
