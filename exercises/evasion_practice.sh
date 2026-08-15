#!/usr/bin/env bash
set -euo pipefail

AURORA_URL="${AURORA_URL:-http://127.0.0.1:5000}"
KIBANA_URL="${KIBANA_URL:-http://127.0.0.1:5601}"
TIMEOUT="${EVASION_TIMEOUT:-120}"
WAIT_SECONDS="${DETECTION_WAIT_SECONDS:-15}"

log() {
  printf '[evasion-practice] %s\n' "$*"
}

fail() {
  printf '[evasion-practice] ERROR: %s\n' "$*" >&2
  exit 1
}

alert_count() {
  local response
  response="$(curl --silent --show-error --fail --max-time 20 \
    --header 'kbn-xsrf: true' \
    --header 'Content-Type: application/json' \
    --request POST \
    --data '{"query":{"bool":{"filter":[{"range":{"@timestamp":{"gte":"now-15m"}}}]}},"size":0}' \
    "${KIBANA_URL%/}/api/detection_engine/signals/search" 2>/dev/null || true)"
  printf '%s' "$response" | sed -n 's/.*"total"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' | head -1
}

if [[ "${RUNTIME:-0}" != "1" ]]; then
  log "Static/VPS mode: no Aurora or Kibana requests will run"
  log "Local execution: RUNTIME=1 ./exercises/evasion_practice.sh"
  exit 0
fi

command -v curl >/dev/null 2>&1 || fail "RUNTIME=1 requires curl"
before="$(alert_count)"
before="${before:-0}"

log "Noisy query: expected to trigger E01"
curl --silent --show-error --fail --max-time "$TIMEOUT" \
  --header 'Content-Type: application/json' \
  --request POST \
  --data '{"query":"What documents do you have?","session_id":"evasion-noisy","user_id":"evasion"}' \
  "${AURORA_URL%/}/api/chat" >/dev/null

log "Stealthy query: expected not to trigger E01"
curl --silent --show-error --fail --max-time "$TIMEOUT" \
  --header 'Content-Type: application/json' \
  --request POST \
  --data '{"query":"Which section covers vacation policy?","session_id":"evasion-stealthy","user_id":"evasion"}' \
  "${AURORA_URL%/}/api/chat" >/dev/null

log "Waiting ${WAIT_SECONDS}s for Kibana detection processing"
sleep "$WAIT_SECONDS"
after="$(alert_count)"
after="${after:-0}"
detected=$((after - before))
((detected < 0)) && detected=0
printf '[evasion-practice] Alerts before=%s after=%s new=%s\n' "$before" "$after" "$detected"
printf '[evasion-practice] Detection rate denominator: 1 noisy query + 1 stealthy query; inspect Kibana for E01 attribution\n'
