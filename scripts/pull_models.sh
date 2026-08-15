#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
BONSAI_MODEL_FILE="${BONSAI_MODEL_FILE:-${PROJECT_ROOT}/models/bonsai-27b.gguf}"

log() {
  printf '[model-check] %s\n' "$*"
}

fail() {
  printf '[model-check] ERROR: %s\n' "$*" >&2
  exit 1
}

if [[ ! -s "$BONSAI_MODEL_FILE" ]]; then
  shopt -s nullglob
  candidates=("${PROJECT_ROOT}/models/"*.gguf)
  shopt -u nullglob
  if [[ ${#candidates[@]} -eq 1 ]]; then
    BONSAI_MODEL_FILE="${candidates[0]}"
    log "Using the only local GGUF: ${BONSAI_MODEL_FILE}"
  else
    fail "Missing Bonsai GGUF. Set BONSAI_MODEL_FILE or place exactly one .gguf under ${PROJECT_ROOT}/models"
  fi
fi

log "Bonsai model exists: ${BONSAI_MODEL_FILE}"
log "No model pull is performed. The lab uses the pre-downloaded PrismML Bonsai file."
