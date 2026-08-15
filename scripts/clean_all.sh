#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"

if [[ "${RUNTIME:-0}" != "1" ]]; then
  printf '[clean-all] Static/VPS mode: no containers, volumes, or files will be removed\n'
  printf '[clean-all] Local execution requires RUNTIME=1 and CONFIRM_CLEAN=1\n'
  exit 0
fi

if [[ "${CONFIRM_CLEAN:-0}" != "1" ]]; then
  printf '[clean-all] Refusing cleanup without CONFIRM_CLEAN=1\n' >&2
  exit 1
fi

command -v docker >/dev/null 2>&1 || { printf '[clean-all] ERROR: docker is required\n' >&2; exit 1; }
printf '[clean-all] Removing Compose containers, networks, and named volumes\n'
docker compose -f "$COMPOSE_FILE" down --volumes --remove-orphans
printf '[clean-all] Local model files and source data were preserved\n'
