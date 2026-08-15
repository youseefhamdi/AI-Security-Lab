#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
RAW_OUTPUT=false
[[ "${1:-}" == "--raw" ]] && RAW_OUTPUT=true

if [[ "${RUNTIME:-0}" != "1" ]]; then
  printf 'Static/VPS mode: model-provider discovery is disabled.\n'
  printf 'Local execution: RUNTIME=1 ./scripts/discover_models.sh\n'
  exit 0
fi

OLLAMA_CONTAINER="${LLAMA_CONTAINER:-}"
OLLAMA_URL="${LLAMA_URL:-http://127.0.0.1:11434}"
BONSAI_URL="${BONSAI_URL:-http://127.0.0.1:11435}"
LM_STUDIO_URL="${LM_STUDIO_URL:-http://127.0.0.1:1234}"
LLAMACPP_URL="${LLAMACPP_URL:-http://127.0.0.1:8080}"
VLLM_URL="${VLLM_URL:-http://127.0.0.1:8000}"

# Each candidate is: provider<TAB>model name<TAB>source endpoint or local path.
declare -a CANDIDATES=()

add_candidate() {
  local provider="$1"
  local model="$2"
  local source="$3"
  local candidate="${provider}"$'\t'"${model}"$'\t'"${source}"
  local existing

  [[ -n "$model" ]] || return 0
  for existing in "${CANDIDATES[@]}"; do
    [[ "$existing" == "$candidate" ]] && return 0
  done
  CANDIDATES+=("$candidate")
}

add_openai_endpoint_models() {
  local provider="$1"
  local base_url="$2"
  local response
  local model

  response="$(curl \
    --silent \
    --show-error \
    --fail \
    --connect-timeout 2 \
    --max-time 5 \
    --header 'Accept: application/json' \
    "${base_url}/v1/models" 2>/dev/null || true)"
  [[ -n "$response" ]] || return 0

  while IFS= read -r model; do
    [[ -n "$model" ]] && add_candidate "$provider" "$model" "$base_url"
  done < <(
    printf '%s' "$response" |
      grep -oE '"id"[[:space:]]*:[[:space:]]*"[^"]+"' |
      sed -E 's/.*"id"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/'
  )
}

# Optional models currently available inside an Ollama container.
if [[ -n "$OLLAMA_CONTAINER" ]] && command -v docker >/dev/null 2>&1 && docker inspect "$OLLAMA_CONTAINER" >/dev/null 2>&1; then
  while IFS= read -r model; do
    [[ -n "$model" ]] && add_candidate "ollama-lab" "$model" "$OLLAMA_URL"
  done < <(docker exec "$OLLAMA_CONTAINER" ollama list 2>/dev/null | awk 'NR > 1 && NF {print $1}')
fi

# Models exposed by active OpenAI-compatible providers.
add_openai_endpoint_models "bonsai" "$BONSAI_URL"
add_openai_endpoint_models "lm-studio" "$LM_STUDIO_URL"
add_openai_endpoint_models "llama.cpp" "$LLAMACPP_URL"
add_openai_endpoint_models "vllm" "$VLLM_URL"
if [[ -n "${OPENAI_BASE_URL:-}" ]]; then
  add_openai_endpoint_models "openai-compatible" "${OPENAI_BASE_URL%/}"
fi

# Local files/caches are inventory-only candidates. They are not selected as
# runnable providers unless a corresponding server endpoint is also active.
add_cache_files() {
  local provider="$1"
  local root="$2"
  local pattern="$3"
  local path
  local relative

  [[ -d "$root" ]] || return 0
  while IFS= read -r path; do
    relative="${path#"${root}"/}"
    add_candidate "$provider" "$relative" "$path"
  done < <(find "$root" -type f -iname "$pattern" -print 2>/dev/null)
}

add_cache_files "gguf-file" "${PROJECT_ROOT}/models" '*.gguf'
add_cache_files "lm-studio-cache" "${HOME}/.cache/lm-studio/models" '*.gguf'
add_cache_files "huggingface-cache" "${HOME}/.cache/huggingface/hub" '*.gguf'
add_cache_files "llama.cpp-cache" "${HOME}/.cache/llama.cpp" '*.gguf'

if [[ -d "${OLLAMA_MODELS_DIR:-${HOME}/.ollama/models/manifests}" ]]; then
  OLLAMA_CACHE="${OLLAMA_MODELS_DIR:-${HOME}/.ollama/models/manifests}"
  while IFS= read -r path; do
    relative="${path#"${OLLAMA_CACHE}"/}"
    add_candidate "ollama-cache" "$relative" "$path"
  done < <(find "$OLLAMA_CACHE" -type f -print 2>/dev/null)
fi

if [[ "$RAW_OUTPUT" == true ]]; then
  printf '%b\n' "${CANDIDATES[@]}"
else
  if ((${#CANDIDATES[@]} == 0)); then
    printf 'No local models detected.\n'
  else
    printf 'Detected local models/providers:\n'
    index=1
    for candidate in "${CANDIDATES[@]}"; do
      printf '  %d. %b\n' "$index" "$candidate"
      ((index += 1))
    done
  fi
fi
