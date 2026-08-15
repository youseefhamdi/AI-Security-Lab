#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
PULL_MODELS="${PULL_MODELS:-0}"
SEED_DATA="${SEED_DATA:-1}"

log() {
  printf '[start-all] %s\n' "$*"
}

fail() {
  printf '[start-all] ERROR: %s\n' "$*" >&2
  exit 1
}

if [[ "${RUNTIME:-0}" != "1" ]]; then
  log "Static/VPS mode: no Docker startup, model pull, seed, or network action will run"
  log "Local execution: RUNTIME=1 ./scripts/start_all.sh"
  exit 0
fi

command -v docker >/dev/null 2>&1 || fail "RUNTIME=1 requires docker"
docker compose -f "$COMPOSE_FILE" config >/dev/null

available_services="$(docker compose -f "$COMPOSE_FILE" config --services)"
service_available() {
  printf '%s\n' "$available_services" | grep -Fxq "$1"
}

start_group() {
  local group="$1"
  shift
  local service
  log "Starting ${group}"
  for service in "$@"; do
    if service_available "$service"; then
      docker compose -f "$COMPOSE_FILE" up -d "$service"
    else
      log "Skipping unavailable Compose service: ${service}"
    fi
  done
}

start_group "infrastructure" kong-database kong-migration kong milvus chromadb redis
start_group "SIEM" elasticsearch kibana filebeat
start_group "inference" ollama-llama bonsai

if [[ "$PULL_MODELS" == "1" ]]; then
  log "PULL_MODELS=1: invoking the explicitly requested local model setup"
  RUNTIME=1 ./scripts/pull_models.sh
else
  log "Skipping model pulls; using pre-existing local model assets"
fi

start_group "protocols" a2a-agent a2a-router a2a-knowledge mcp-server mcp-wrapper
start_group "applications" aurora phoenix assistant
start_group "memory" mem0
start_group "LightRAG" lightrag

if [[ "$SEED_DATA" == "1" ]]; then
  log "Seeding storage and memories"
  RUNTIME=1 python3 ./scripts/seed_storage.py
  RUNTIME=1 python3 ./scripts/seed_memories.py
else
  log "SEED_DATA=0: skipping storage and memory seeding"
fi

log "Configuring Kong"
RUNTIME=1 ./scripts/configure_kong.sh
log "Creating detection rules"
RUNTIME=1 ./scripts/create_detection_rules.sh
log "All available lab phases started"
