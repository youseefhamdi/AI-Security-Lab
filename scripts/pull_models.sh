#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
BONSAI_MODEL_FILE="${BONSAI_MODEL_FILE:-${PROJECT_ROOT}/models/bonsai-27b.gguf}"
OLLAMA_CONTAINER="${OLLAMA_CONTAINER:-lab-ollama-llama}"
OLLAMA_MODEL="${OLLAMA_MODEL:-llama3.2:1b}"

log() {
  printf '[pull-models] %s\n' "$*"
}

fail() {
  printf '[pull-models] ERROR: %s\n' "$*" >&2
  exit 1
}

check_bonsai_model() {
  if [[ ! -s "$BONSAI_MODEL_FILE" ]]; then
    fail "Missing Bonsai model: ${BONSAI_MODEL_FILE}\nDownload it from your authorized model source, then run:\n  curl -L '<AUTHORIZED_BONSAI_GGUF_URL>' -o '${BONSAI_MODEL_FILE}'"
  fi

  log "Bonsai model exists: ${BONSAI_MODEL_FILE}"
}

runtime_pull_ollama() {
  if [[ "${RUNTIME:-0}" != "1" ]]; then
    log "Static/VPS mode: RUNTIME is not 1; no Docker commands or model pulls will run"
    return 0
  fi

  command -v docker >/dev/null 2>&1 || fail "RUNTIME=1 requires docker"
  log "Runtime mode enabled; checking ${OLLAMA_MODEL} in ${OLLAMA_CONTAINER}"

  if docker exec "$OLLAMA_CONTAINER" ollama list 2>/dev/null | grep -Fq "$OLLAMA_MODEL"; then
    log "${OLLAMA_MODEL} already exists; skipping pull"
    return 0
  fi

  log "Pulling ${OLLAMA_MODEL} into ${OLLAMA_CONTAINER}"
  docker exec "$OLLAMA_CONTAINER" ollama pull "$OLLAMA_MODEL"
  docker exec "$OLLAMA_CONTAINER" ollama list | grep -Fq "$OLLAMA_MODEL" \
    || fail "${OLLAMA_MODEL} was not found after the runtime pull"
  log "${OLLAMA_MODEL} is available"
}

check_bonsai_model
runtime_pull_ollama
log "Model checks complete"
