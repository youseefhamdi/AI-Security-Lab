#!/usr/bin/env bash
set -euo pipefail

KONG_ADMIN_URL="${KONG_ADMIN_URL:-http://127.0.0.1:8001}"
MAX_ATTEMPTS="${KONG_MAX_ATTEMPTS:-60}"
SLEEP_SECONDS="${KONG_POLL_INTERVAL:-2}"

log() {
  printf '[wait-for-kong] %s\n' "$*"
}

log "Waiting for Kong Admin API at ${KONG_ADMIN_URL}"

for ((attempt = 1; attempt <= MAX_ATTEMPTS; attempt++)); do
  if curl \
    --silent \
    --show-error \
    --fail \
    --connect-timeout 2 \
    --max-time 5 \
    --header 'Accept: application/json' \
    "${KONG_ADMIN_URL}/" >/dev/null 2>&1; then
    log "Kong Admin API is ready (attempt ${attempt}/${MAX_ATTEMPTS})"
    exit 0
  fi

  log "Kong is not ready (attempt ${attempt}/${MAX_ATTEMPTS}); retrying in ${SLEEP_SECONDS}s"
  sleep "$SLEEP_SECONDS"
done

printf '[wait-for-kong] ERROR: Kong Admin API did not become ready after %s attempts\n' "$MAX_ATTEMPTS" >&2
exit 1
