#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

LLAMA_CONTAINER="${LLAMA_CONTAINER:-lab-ollama-llama}"
LLAMA_URL="${LLAMA_URL:-http://127.0.0.1:11434}"
BONSAI_CONTAINER="${BONSAI_CONTAINER:-lab-bonsai}"
BONSAI_URL="${BONSAI_URL:-http://127.0.0.1:11435}"
LLAMA_MODEL="${LLAMA_MODEL:-llama3.2:1b}"
BONSAI_MODEL_FILE="${BONSAI_MODEL_FILE:-${PROJECT_ROOT}/models/bonsai-27b.gguf}"
POLL_INTERVAL="${POLL_INTERVAL:-10}"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-180}"
OLLAMA_PULL_TIMEOUT="${OLLAMA_PULL_TIMEOUT:-45m}"
INFERENCE_TIMEOUT="${INFERENCE_TIMEOUT:-300}"

log() {
  printf '[pull-models] %s\n' "$*"
}

fail() {
  printf '[pull-models] ERROR: %s\n' "$*" >&2
  exit 1
}

wait_for_endpoint() {
  local label="$1"
  local url="$2"
  local endpoint="$3"

  log "Waiting for ${label} at ${url}${endpoint}"
  for ((attempt = 1; attempt <= MAX_ATTEMPTS; attempt++)); do
    if curl \
      --silent \
      --show-error \
      --fail \
      --connect-timeout 5 \
      --max-time 10 \
      --header 'Accept: application/json' \
      "${url}${endpoint}" >/dev/null 2>&1; then
      log "${label} is healthy (attempt ${attempt}/${MAX_ATTEMPTS})"
      return 0
    fi

    log "${label} is not ready (attempt ${attempt}/${MAX_ATTEMPTS}); retrying in ${POLL_INTERVAL}s"
    sleep "$POLL_INTERVAL"
  done

  fail "Timed out waiting for ${label} after $((MAX_ATTEMPTS * POLL_INTERVAL)) seconds"
}

json_escape() {
  local value="$1"
  value=${value//\\/\\\\}
  value=${value//\"/\\\"}
  value=${value//$'\n'/\\n}
  value=${value//$'\r'/\\r}
  printf '%s' "$value"
}

test_ollama_inference() {
  local model="$1"
  local prompt='Reply with exactly the word READY.'
  local payload
  local response

  payload="{\"model\":\"$(json_escape "$model")\",\"prompt\":\"$(json_escape "$prompt")\",\"stream\":false,\"options\":{\"num_predict\":8}}"
  log "Testing inference on Ollama model ${model}"
  response="$(curl \
    --silent \
    --show-error \
    --fail \
    --connect-timeout 5 \
    --max-time "$INFERENCE_TIMEOUT" \
    --header 'Accept: application/json' \
    --header 'Content-Type: application/json' \
    --data "$payload" \
    "${LLAMA_URL}/api/generate")" \
    || fail "Inference request failed for Ollama model ${model}"

  printf '%s\n' "$response"
  printf '%s' "$response" | grep -Eq '"response"[[:space:]]*:' \
    || fail "Unexpected Ollama inference response shape"
}

test_openai_inference() {
  local model="$1"
  local base_url="$2"
  local payload
  local response

  payload="{\"model\":\"$(json_escape "$model")\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly the word READY.\"}],\"max_tokens\":8,\"temperature\":0}"
  log "Testing inference on OpenAI-compatible model ${model} at ${base_url}"
  response="$(curl \
    --silent \
    --show-error \
    --fail \
    --connect-timeout 5 \
    --max-time "$INFERENCE_TIMEOUT" \
    --header 'Accept: application/json' \
    --header 'Content-Type: application/json' \
    --data "$payload" \
    "${base_url}/v1/chat/completions")" \
    || fail "Inference request failed for OpenAI-compatible model ${model}"

  printf '%s\n' "$response"
  printf '%s' "$response" | grep -Eq '"choices"[[:space:]]*:' \
    || fail "Unexpected OpenAI-compatible inference response shape"
}

ollama_model_exists() {
  local model="$1"
  docker exec "$LLAMA_CONTAINER" ollama list 2>/dev/null | grep -Fq "$model"
}

pull_ollama_model() {
  if ollama_model_exists "$LLAMA_MODEL"; then
    log "${LLAMA_MODEL} already exists in ${LLAMA_CONTAINER}; skipping pull"
    return
  fi

  log "Pulling ${LLAMA_MODEL} into ${LLAMA_CONTAINER} (timeout: ${OLLAMA_PULL_TIMEOUT})"
  log "Pull output will stream below; rerunning this script is safe if interrupted"
  if timeout --foreground "$OLLAMA_PULL_TIMEOUT" \
    docker exec "$LLAMA_CONTAINER" ollama pull "$LLAMA_MODEL"; then
    log "Finished pulling ${LLAMA_MODEL}"
  else
    local status=$?
    if [[ "$status" -eq 124 ]]; then
      fail "Timed out pulling ${LLAMA_MODEL} after ${OLLAMA_PULL_TIMEOUT}; rerun to resume"
    fi
    fail "Ollama pull failed for ${LLAMA_MODEL} with exit code ${status}"
  fi

  ollama_model_exists "$LLAMA_MODEL" || fail "${LLAMA_MODEL} is not present after pull"
}

verify_ollama_model() {
  log "Verifying ${LLAMA_MODEL} in ${LLAMA_CONTAINER}"
  docker exec "$LLAMA_CONTAINER" ollama list
  ollama_model_exists "$LLAMA_MODEL" || fail "Could not verify ${LLAMA_MODEL} with ollama list"
}

verify_bonsai_model() {
  [[ -s "$BONSAI_MODEL_FILE" ]] || fail "Bonsai model file not found or empty at ${BONSAI_MODEL_FILE}"

  log "Verifying Bonsai model file: ${BONSAI_MODEL_FILE}"
  docker exec "$BONSAI_CONTAINER" test -f /models/bonsai-27b.gguf \
    || fail "${BONSAI_CONTAINER} cannot access /models/bonsai-27b.gguf"

  log "Verifying Bonsai OpenAI-compatible model endpoint"
  curl \
    --silent \
    --show-error \
    --fail \
    --connect-timeout 5 \
    --max-time 15 \
    --header 'Accept: application/json' \
    "${BONSAI_URL}/v1/models"
  printf '\n'
}

select_existing_model() {
  local candidates candidate provider model source
  local selected_provider="${MODEL_PROVIDER:-}"
  local selected_model="${MODEL_NAME:-}"
  local selected_source=""

  mapfile -t candidates < <("${SCRIPT_DIR}/discover_models.sh" --raw)
  if ((${#candidates[@]} == 0)); then
    log "Discovery found no local models; pulling is permitted"
    return 1
  fi

  log "Discovery found existing models/providers; no model will be pulled"
  for candidate in "${candidates[@]}"; do
    printf '  %b\n' "$candidate"
  done

  if [[ -z "$selected_provider" || -z "$selected_model" ]]; then
    fail "Set MODEL_PROVIDER and MODEL_NAME to explicitly select an existing model; no pull was attempted"
  fi

  for candidate in "${candidates[@]}"; do
    IFS=$'\t' read -r provider model source <<< "$candidate"
    if [[ "$provider" == "$selected_provider" && "$model" == "$selected_model" ]]; then
      selected_source="$source"
      break
    fi
  done

  [[ -n "$selected_source" ]] || fail "Requested model was not found: provider=${selected_provider}, model=${selected_model}; no pull was attempted"
  log "Using selected existing model: provider=${selected_provider}, model=${selected_model}"

  case "$selected_provider" in
    ollama-lab)
      LLAMA_URL="$selected_source"
      wait_for_endpoint "selected Ollama provider" "$LLAMA_URL" "/api/tags"
      test_ollama_inference "$selected_model"
      ;;
    bonsai|lm-studio|llama.cpp|vllm|openai-compatible)
      wait_for_endpoint "selected ${selected_provider} provider" "$selected_source" "/v1/models"
      test_openai_inference "$selected_model" "$selected_source"
      ;;
    *)
      fail "${selected_provider}/${selected_model} is a cache or local file, not a running provider; start a compatible provider and select its active provider entry"
      ;;
  esac

  log "Existing selected model verified; no pull was performed"
  return 0
}

if select_existing_model; then
  log "Model setup complete without downloading"
  exit 0
fi

wait_for_endpoint "${LLAMA_CONTAINER}" "$LLAMA_URL" "/api/tags"
pull_ollama_model
verify_ollama_model
test_ollama_inference "$LLAMA_MODEL"

wait_for_endpoint "${BONSAI_CONTAINER}" "$BONSAI_URL" "/health"
verify_bonsai_model
test_openai_inference "bonsai" "$BONSAI_URL"

log "Model setup and inference checks complete"
