#!/usr/bin/env bash
# Source this file from a runtime script. It exports the provider settings that
# Compose passes into the applications. It never downloads models.

provider_log() {
  printf '[provider] %s\n' "$*"
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
  export INFERENCE_BASE_URL="http://host.docker.internal:11434/v1"
  export INFERENCE_LOCAL_BASE_URL="${local_base%/}/v1"
  export INFERENCE_MODEL="${INFERENCE_MODEL:-$model}"
  provider_log "Detected Ollama: model=${INFERENCE_MODEL}, endpoint=${local_base}"
  return 0
}

provider_select_bonsai() {
  export INFERENCE_PROVIDER="bonsai"
  export INFERENCE_BASE_URL="http://bonsai:8000/v1"
  export INFERENCE_LOCAL_BASE_URL="http://127.0.0.1:11435/v1"
  export INFERENCE_MODEL="${INFERENCE_MODEL:-bonsai-27b}"
  provider_log "Using local Bonsai fallback: model=${INFERENCE_MODEL}"
}

# Static/VPS mode must not probe localhost or make network requests.
if [[ "${RUNTIME:-0}" != "1" ]]; then
  provider_select_bonsai
  return 0 2>/dev/null || exit 0
fi

requested_provider="${INFERENCE_PROVIDER:-auto}"
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
  if provider_probe_openai "lmstudio" "http://127.0.0.1:1234/v1" "http://host.docker.internal:1234/v1"; then
    return 0 2>/dev/null || exit 0
  fi
  [[ "$requested_provider" == "lmstudio" || "$requested_provider" == "lms" ]] && provider_log "LM Studio was not reachable; continuing provider discovery"
fi

if [[ "$requested_provider" == "auto" || "$requested_provider" == "llamacpp" ]]; then
  if provider_probe_openai "llamacpp" "http://127.0.0.1:11435/v1" "http://host.docker.internal:11435/v1"; then
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
