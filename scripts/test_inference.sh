#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REQUEST_TIMEOUT="${REQUEST_TIMEOUT:-300}"

log() {
  printf '[test-inference] %s\n' "$*"
}

fail() {
  printf '[test-inference] ERROR: %s\n' "$*" >&2
  exit 1
}

if [[ "${RUNTIME:-0}" != "1" ]]; then
  log "Static/VPS mode: RUNTIME is not 1; no provider probing or network requests will run"
  log "Local execution: RUNTIME=1 ./scripts/test_inference.sh"
  exit 0
fi

command -v curl >/dev/null 2>&1 || fail "RUNTIME=1 requires curl"
source "${SCRIPT_DIR}/detect_provider.sh"

TEST_ENDPOINT="${INFERENCE_LOCAL_BASE_URL}/chat/completions"
TEST_MODEL="${INFERENCE_MODEL}"
payload="{\"model\":\"${TEST_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply: BACKEND_OK\"}],\"temperature\":0,\"max_tokens\":16,\"stream\":false}"
log "Testing provider=${INFERENCE_PROVIDER}, model=${TEST_MODEL}, endpoint=${TEST_ENDPOINT}"
response="$(curl --silent --show-error --fail --connect-timeout 5 --max-time "$REQUEST_TIMEOUT" \
  --header 'Accept: application/json' --header 'Content-Type: application/json' \
  --data "$payload" "$TEST_ENDPOINT")" || fail "Inference provider request failed"
printf '%s\n' "$response"
printf '%s' "$response" | grep -Eq '"choices"[[:space:]]*:' || fail "Response did not contain OpenAI choices"
printf '%s' "$response" | grep -Eq 'BACKEND_OK' || fail "Response did not echo BACKEND_OK"
log "${INFERENCE_PROVIDER} response shape and BACKEND_OK echo verified"
