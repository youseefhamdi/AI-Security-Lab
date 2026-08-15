#!/usr/bin/env bash

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

# Source this file from a runtime script. It exports the provider settings that
# Compose passes into the applications. It never downloads models.

provider_log() {
  printf '[provider] %s\n' "$*"
}

# Docker uses host.docker.internal; Podman commonly uses
# host.containers.internal. Set INFERENCE_CONTAINER_HOST explicitly when the
# host provider is running outside the containers.
provider_container_host() {
  printf '%s' "${INFERENCE_CONTAINER_HOST:-host.docker.internal}"
}

provider_load_model_settings() {
  local env_file="${PROJECT_ROOT}/.env"
  local value

  [[ -f "$env_file" ]] || return 0

  if [[ -z "${BONSAI_MODEL_DIR:-}" ]]; then
    value="$(sed -nE 's/^[[:space:]]*BONSAI_MODEL_DIR=[[:space:]]*([^#]+).*$/\1/p' "$env_file" | tail -n 1)"
    value="${value%$'\r'}"
    [[ -n "$value" ]] && export BONSAI_MODEL_DIR="$value"
  fi

  if [[ -z "${BONSAI_MODEL_FILE:-}" ]]; then
    value="$(sed -nE 's/^[[:space:]]*BONSAI_MODEL_FILE=[[:space:]]*([^#]+).*$/\1/p' "$env_file" | tail -n 1)"
    value="${value%$'\r'}"
    [[ -n "$value" ]] && export BONSAI_MODEL_FILE="$value"
  fi
}

provider_find_bonsai_model() {
  provider_load_model_settings

  local requested="${BONSAI_MODEL_FILE:-bonsai-27b.gguf}"
  local candidate relative model_dir
  local -a search_dirs=()

  if [[ -n "${BONSAI_MODEL_DIR:-}" ]]; then
    # An explicit directory is authoritative; do not search unrelated paths.
    search_dirs+=("${BONSAI_MODEL_DIR}")
  else
    # Portable defaults: project-local models first, then common LM Studio
    # locations under the current user's home directory.
    search_dirs+=("${PROJECT_ROOT}/models")
    if [[ -n "${HOME:-}" ]]; then
      search_dirs+=(
        "${HOME}/.lmstudio/hub/models"
        "${HOME}/.lmstudio/models"
        "${HOME}/.cache/lm-studio/models"
        "${HOME}/.cache/lmstudio/models"
      )
    fi
  fi

  if [[ "$requested" = /* && -s "$requested" ]]; then
    export BONSAI_MODEL_DIR="$(dirname -- "$requested")"
    export BONSAI_MODEL_FILE="$(basename -- "$requested")"
    return 0
  fi

  for model_dir in "${search_dirs[@]}"; do
    model_dir="${model_dir%/}"
    [[ -d "$model_dir" ]] || continue

    if [[ "$requested" != /* && -s "${model_dir}/${requested}" ]]; then
      export BONSAI_MODEL_DIR="$model_dir"
      export BONSAI_MODEL_FILE="$requested"
      return 0
    fi

    candidate="$(find "$model_dir" -type f -iname '*.gguf' -print -quit 2>/dev/null || true)"
    if [[ -n "$candidate" ]]; then
      relative="${candidate#"${model_dir}/"}"
      export BONSAI_MODEL_DIR="$model_dir"
      export BONSAI_MODEL_FILE="$relative"
      provider_log "Discovered Bonsai GGUF: ${candidate}"
      return 0
    fi
  done

  export BONSAI_MODEL_DIR="${BONSAI_MODEL_DIR:-${PROJECT_ROOT}/models}"
  export BONSAI_MODEL_FILE="$requested"
  return 1
}

provider_model_from_json() {
  printf '%s' "$1" \
    | grep -oE '"(id|name|model)"[[:space:]]*:[[:space:]]*"[^"]+"' \
    | head -n 1 \
    | sed -E 's/.*"(id|name|model)"[[:space:]]*:[[:space:]]*"([^"]+)"/\2/'
}

provider_probe_openai() {
  local provider="$1"
  local local_base="$2"
  local container_base="$3"
  local response model

  response="$(curl --silent --show-error --fail --connect-timeout 2 --max-time 5 \
    --header 'Accept: application/json' "${local_base%/}/models" 2>/dev/null || true)"
  [[ -n "$response" ]] || return 1
  model="$(provider_model_from_json "$response")"
  [[ -n "$model" ]] || return 1

  export INFERENCE_PROVIDER="$provider"
  export INFERENCE_BASE_URL="$container_base"
  export INFERENCE_LOCAL_BASE_URL="$local_base"
  export INFERENCE_MODEL="${INFERENCE_MODEL:-$model}"
  provider_log "Detected ${provider}: model=${INFERENCE_MODEL}, endpoint=${local_base}"
  return 0
}

provider_probe_ollama() {
  local local_base="${OLLAMA_URL:-http://127.0.0.1:11434}"
  local response model

  response="$(curl --silent --show-error --fail --connect-timeout 2 --max-time 5 \
    --header 'Accept: application/json' "${local_base%/}/api/tags" 2>/dev/null || true)"
  [[ -n "$response" ]] || return 1
  model="$(provider_model_from_json "$response")"
  [[ -n "$model" ]] || return 1

  export INFERENCE_PROVIDER="ollama"
  export INFERENCE_BASE_URL="http://$(provider_container_host):11434/v1"
  export INFERENCE_LOCAL_BASE_URL="${local_base%/}/v1"
  export INFERENCE_MODEL="${INFERENCE_MODEL:-$model}"
  provider_log "Detected Ollama: model=${INFERENCE_MODEL}, endpoint=${local_base}"
  return 0
}

provider_select_bonsai() {
  provider_find_bonsai_model || true
  export INFERENCE_PROVIDER="bonsai"
  export INFERENCE_BASE_URL="http://bonsai:8000/v1"
  export INFERENCE_LOCAL_BASE_URL="http://127.0.0.1:11435/v1"
  export INFERENCE_MODEL="${INFERENCE_MODEL:-bonsai-27b}"
  provider_log "Using local Bonsai fallback: model=${INFERENCE_MODEL}, file=${BONSAI_MODEL_DIR}/${BONSAI_MODEL_FILE}"
}

# Static/VPS mode must not probe localhost or make network requests.
if [[ "${RUNTIME:-0}" != "1" ]]; then
  provider_select_bonsai
  return 0 2>/dev/null || exit 0
fi

requested_provider="${INFERENCE_PROVIDER:-auto}"
provider_load_model_settings

# An explicitly configured local model directory is an intentional provider
# choice. Prefer it over auto-detected host services such as Ollama.
if [[ "$requested_provider" == "auto" && -n "${BONSAI_MODEL_DIR:-}" ]]; then
  if provider_find_bonsai_model; then
    provider_log "Configured Bonsai model directory found; preferring local Bonsai over auto-detected providers"
    provider_select_bonsai
    return 0 2>/dev/null || exit 0
  fi
  provider_log "Configured Bonsai directory has no readable GGUF; continuing provider discovery"
fi

if [[ "$requested_provider" == "bonsai" ]]; then
  provider_select_bonsai
  return 0 2>/dev/null || exit 0
fi

if [[ "$requested_provider" == "auto" || "$requested_provider" == "ollama" ]]; then
  if provider_probe_ollama; then
    return 0 2>/dev/null || exit 0
  fi
  [[ "$requested_provider" == "ollama" ]] && provider_log "Ollama was not reachable; continuing provider discovery"
fi

if [[ "$requested_provider" == "auto" || "$requested_provider" == "lmstudio" || "$requested_provider" == "lms" ]]; then
  if provider_probe_openai "lmstudio" "http://127.0.0.1:1234/v1" "http://$(provider_container_host):1234/v1"; then
    return 0 2>/dev/null || exit 0
  fi
  [[ "$requested_provider" == "lmstudio" || "$requested_provider" == "lms" ]] && provider_log "LM Studio was not reachable; continuing provider discovery"
fi

if [[ "$requested_provider" == "auto" || "$requested_provider" == "llamacpp" ]]; then
  if provider_probe_openai "llamacpp" "http://127.0.0.1:11435/v1" "http://$(provider_container_host):11435/v1"; then
    return 0 2>/dev/null || exit 0
  fi
fi

if [[ -n "${INFERENCE_DISCOVERY_URL:-}" ]]; then
  custom_url="${INFERENCE_DISCOVERY_URL%/}"
  if provider_probe_openai "openai-compatible" "$custom_url" "${INFERENCE_CONTAINER_URL:-$custom_url}"; then
    return 0 2>/dev/null || exit 0
  fi
fi

provider_log "No external provider detected; selecting the local Bonsai container"
provider_select_bonsai
return 0 2>/dev/null || exit 0
