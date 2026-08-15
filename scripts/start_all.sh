#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
LAB_MODE="${LAB_MODE:-core}"
SEED_DATA="${SEED_DATA:-0}"

print_brand() {
  printf '\n'
  cat <<'BANNER'
  ╔══════════════════════════════════════════════════════════════╗
  ║                     Z O D I A C                              ║
  ║                 AI SECURITY LAB                               ║
  ║                                                              ║
  ║                         .-''''-.                             ║
  ║                      .-'  _  _  '-.                          ║
  ║                    .'   / \\/ \\   '.                        ║
  ║                   /    |  /\\  |    \\                       ║
  ║                  ;     | /==\\ |     ;                       ║
  ║                  |     | \\__/ |     |                       ║
  ║                  ;      \\____/      ;                       ║
  ║                   \\       ||       /                        ║
  ║                    '.     /||\\    .'                         ║
  ║                      '-._||||_.-'                           ║
  ║                         /||||\\                             ║
  ║                  RECON · ATTACK · DETECT                     ║
  ╚══════════════════════════════════════════════════════════════╝
BANNER
}

log() {
  printf '[start-all] %s\n' "$*"
}

fail() {
  printf '[start-all] ERROR: %s\n' "$*" >&2
  exit 1
}

print_brand

if [[ "${RUNTIME:-0}" != "1" ]]; then
  log "Static/VPS mode: no Docker startup, model pull, seed, or network action will run"
  log "Local core mode: RUNTIME=1 ./scripts/start_all.sh"
  log "Local lite mode: RUNTIME=1 LAB_MODE=lite ./scripts/start_all.sh"
  log "Local full mode: RUNTIME=1 LAB_MODE=full SEED_DATA=1 ./scripts/start_all.sh"
  exit 0
fi

command -v docker >/dev/null 2>&1 || fail "RUNTIME=1 requires docker"
./scripts/pull_models.sh
docker compose -f "$COMPOSE_FILE" config >/dev/null

case "$LAB_MODE" in
  core)
    log "Starting minimal core: one Bonsai backend, three apps, and local document retrieval"
    docker compose -f "$COMPOSE_FILE" up -d bonsai aurora phoenix assistant
    ;;
  lite)
    log "Starting lite core plus optional A2A and MCP protocol services"
    docker compose -f "$COMPOSE_FILE" --profile protocols up -d
    ;;
  full)
    log "Starting full profile: lite core plus gateway, storage, memory, and SIEM"
    docker compose -f "$COMPOSE_FILE" --profile protocols --profile full up -d
    if [[ "$SEED_DATA" == "1" ]]; then
      log "Seeding full-profile storage and memories"
      RUNTIME=1 python3 ./scripts/seed_storage.py
      RUNTIME=1 python3 ./scripts/seed_memories.py
    else
      log "SEED_DATA=0: skipping storage and memory seeding"
    fi
    log "Configuring Kong and detection rules"
    RUNTIME=1 ./scripts/configure_kong.sh
    RUNTIME=1 ./scripts/create_detection_rules.sh
    ;;
  *)
    fail "LAB_MODE must be core, lite, or full"
    ;;
esac

log "Startup complete; no model pull was requested"
