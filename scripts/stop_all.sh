#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"

if [[ "${RUNTIME:-0}" != "1" ]]; then
  printf '[stop-all] Static/VPS mode: no containers will be stopped\n'
  printf '[stop-all] Local execution: RUNTIME=1 ./scripts/stop_all.sh\n'
  exit 0
fi

command -v docker >/dev/null 2>&1 || { printf '[stop-all] ERROR: docker is required\n' >&2; exit 1; }
printf '[stop-all] Stopping the AI Red Team Lab\n'
docker compose -f "$COMPOSE_FILE" down --remove-orphans
