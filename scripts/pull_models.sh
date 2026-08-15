#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
if [[ "${SKIP_PROVIDER_DETECT:-0}" != "1" ]]; then
  # This is local-only when RUNTIME is unset; an external provider means no
  # local GGUF is required for that run.
  source "${SCRIPT_DIR}/detect_provider.sh"
  if [[ "${INFERENCE_PROVIDER:-bonsai}" != "bonsai" ]]; then
    printf '[model-check] External provider %s is active; skipping local GGUF check\n' "$INFERENCE_PROVIDER"
    exit 0
  fi
fi

BONSAI_MODEL_DIR="${BONSAI_MODEL_DIR:-${PROJECT_ROOT}/models}"
BONSAI_MODEL_FILE="${BONSAI_MODEL_FILE:-bonsai-27b.gguf}"

log() {
  printf '[model-check] %s\n' "$*"
}

fail() {
  printf '[model-check] ERROR: %s\n' "$*" >&2
  exit 1
}

if [[ "$BONSAI_MODEL_FILE" = /* ]]; then
  model_path="$BONSAI_MODEL_FILE"
else
  model_path="${BONSAI_MODEL_DIR%/}/${BONSAI_MODEL_FILE}"
fi

if [[ ! -s "$model_path" ]]; then
  candidate="$(find "${BONSAI_MODEL_DIR%/}" -type f -iname '*.gguf' -print -quit 2>/dev/null || true)"
  if [[ -n "$candidate" ]]; then
    model_path="$candidate"
    log "Discovered GGUF under ${BONSAI_MODEL_DIR}: ${model_path}"
  else
    fail "Missing Bonsai GGUF. Set BONSAI_MODEL_DIR and/or BONSAI_MODEL_FILE, or place a .gguf under ${PROJECT_ROOT}/models"
  fi
fi

log "Bonsai model exists: ${model_path}"
log "No model pull is performed. The lab uses the pre-downloaded PrismML Bonsai file."
