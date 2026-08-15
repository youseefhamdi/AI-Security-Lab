#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
LAB_MODE="${LAB_MODE:-core}"
SEED_DATA="${SEED_DATA:-0}"

print_brand() {
  printf '\n'
  if [[ "${RUNTIME:-0}" == "1" && -t 1 && "${NO_ANIMATION:-0}" != "1" ]]; then
    local frame
    for frame in '◐' '◓' '◑' '◒'; do
      printf '\r  [ZODIAC] Spartan core activation %s' "$frame"
      sleep 0.12
    done
    printf '\033[2K\r'
  fi
  cat <<'BANNER'
  ╔══════════════════════════════════════════════════════════════╗
  ║                     Z O D I A C                              ║
  ║              ZODIAC BANK SECURITY LAB                         ║
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
  log "Static/VPS mode: no provider probing, Docker startup, model pull, seed, or network action will run"
  log "Local core mode: RUNTIME=1 ./scripts/start_all.sh"
  log "Local lite mode: RUNTIME=1 LAB_MODE=lite ./scripts/start_all.sh"
  log "Local full mode: RUNTIME=1 LAB_MODE=full SEED_DATA=1 ./scripts/start_all.sh"
  exit 0
fi

command -v docker >/dev/null 2>&1 || fail "RUNTIME=1 requires docker"
command -v curl >/dev/null 2>&1 || fail "RUNTIME=1 requires curl"

# Exports INFERENCE_PROVIDER, INFERENCE_BASE_URL, INFERENCE_LOCAL_BASE_URL,
# and INFERENCE_MODEL for Compose and all application containers.
# Supported providers: Ollama, LM Studio, llama.cpp, and local Bonsai fallback.
source "${SCRIPT_DIR}/detect_provider.sh"
log "Selected provider=${INFERENCE_PROVIDER}, model=${INFERENCE_MODEL}, endpoint=${INFERENCE_BASE_URL}"

if [[ "$INFERENCE_PROVIDER" == "bonsai" ]]; then
  "${SCRIPT_DIR}/pull_models.sh"
else
  log "Using the already-running ${INFERENCE_PROVIDER} provider; no local model container or pull is needed"
fi

# Full mode intentionally enables the external-context integrations. Set
# ENABLE_EXTERNAL_CONTEXT=0 explicitly to run the services without querying
# LightRAG or Mem0 while debugging them independently.
if [[ "$LAB_MODE" == "full" ]]; then
  export ENABLE_EXTERNAL_CONTEXT="${ENABLE_EXTERNAL_CONTEXT:-1}"
  log "Full profile external context=${ENABLE_EXTERNAL_CONTEXT} (LightRAG + Mem0)"
fi

docker compose -f "$COMPOSE_FILE" config >/dev/null
log "Running offline Zodiac Bank security evaluation"
python3 ./scripts/zodiac_bank_eval.py

core_services=(training-gate training-challenges zodiac-context aurora phoenix assistant)
if [[ "$INFERENCE_PROVIDER" == "bonsai" ]]; then
  core_services=(bonsai "${core_services[@]}")
fi

case "$LAB_MODE" in
  core)
    log "Starting core services: ${core_services[*]}"
    docker compose -f "$COMPOSE_FILE" up -d "${core_services[@]}"
    ;;
  lite)
    log "Starting core plus A2A/MCP protocol services"
    lite_services=("${core_services[@]}" a2a-knowledge a2a-router mcp-server mcp-wrapper)
    docker compose -f "$COMPOSE_FILE" --profile protocols up -d "${lite_services[@]}"
    ;;
  full)
    log "Starting full stack with the selected inference provider"
    full_services=(
      "${core_services[@]}"
      a2a-knowledge a2a-router mcp-server mcp-wrapper
      kong-database kong-migration kong
      chromadb milvus redis lightrag mem0
      a2a-agent mcp-memory mcp-filesystem mcp-fetch
      elasticsearch kibana filebeat
    )
    docker compose -f "$COMPOSE_FILE" --profile protocols --profile full up -d "${full_services[@]}"
    if [[ "$SEED_DATA" == "1" ]]; then
      log "Validating canonical Zodiac Bank domain and orchestrator symmetry"
      python3 ./scripts/validate_zodiac_bank.py
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
