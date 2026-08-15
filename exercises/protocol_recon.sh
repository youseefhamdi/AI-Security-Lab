#!/usr/bin/env bash
set -euo pipefail

ROUTER_URL="${ROUTER_URL:-http://127.0.0.1:4100}"
KNOWLEDGE_URL="${KNOWLEDGE_URL:-http://127.0.0.1:4101}"
MCP_URL="${MCP_URL:-http://127.0.0.1:3001}"

log() {
  printf '[protocol-recon] %s\n' "$*"
}

fail() {
  printf '[protocol-recon] ERROR: %s\n' "$*" >&2
  exit 1
}

if [[ "${RUNTIME:-0}" != "1" ]]; then
  log "Static/VPS mode: RUNTIME is not 1; no protocol requests will run"
  log "Local execution: RUNTIME=1 ./exercises/protocol_recon.sh"
  exit 0
fi

command -v curl >/dev/null 2>&1 || fail "RUNTIME=1 requires curl"

log "Enumerating Router Agent Card"
curl --silent --show-error --fail "$ROUTER_URL/.well-known/agent.json"
printf '\n'

log "Enumerating Knowledge Agent Card"
curl --silent --show-error --fail "$KNOWLEDGE_URL/.well-known/agent.json"
printf '\n'

log "Listing unauthenticated MCP tools"
curl --silent --show-error --fail "$MCP_URL/tools/list"
printf '\n'

log "Calling MCP memory tool through the HTTP wrapper"
curl --silent --show-error --fail \
  --header 'Content-Type: application/json' \
  --request POST \
  --data '{"name":"memory","arguments":{"operation":"read","key":"ZB-CUS-001"}}' \
  "$MCP_URL/tools/call"
printf '\n'

log "Testing Router to Knowledge A2A communication"
curl --silent --show-error --fail \
  --header 'Content-Type: application/json' \
  --request POST \
  --data '{"jsonrpc":"2.0","id":"recon-1","method":"message/send","params":{"message":{"messageId":"recon-message-1","role":"user","parts":[{"kind":"text","text":"How many PTO days does a first-year employee receive?"}]}}}' \
  "$ROUTER_URL/"
printf '\n'

log "Protocol reconnaissance complete"
