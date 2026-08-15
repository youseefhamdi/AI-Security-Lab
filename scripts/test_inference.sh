#!/usr/bin/env bash
set -euo pipefail

LLAMA_URL="${LLAMA_URL:-http://127.0.0.1:11434}"
BONSAI_URL="${BONSAI_URL:-http://127.0.0.1:11435}"
LLAMA_MODEL="${LLAMA_MODEL:-llama3.2:1b}"
BONSAI_API_MODEL="${BONSAI_API_MODEL:-bonsai}"
INFERENCE_TIMEOUT="${INFERENCE_TIMEOUT:-300}"
MODEL_PROVIDER="${MODEL_PROVIDER:-}"
SELECTED_MODEL="${MODEL_NAME:-}"
MODEL_ENDPOINT="${MODEL_ENDPOINT:-}"

log() {
  printf '[test-inference] %s\n' "$*"
}

fail() {
  printf '[test-inference] ERROR: %s\n' "$*" >&2
  exit 1
}

json_escape() {
  local value="$1"
  value=${value//\\/\\\\}
  value=${value//\"/\\\"}
  value=${value//$'\n'/\\n}
  value=${value//$'\r'/\\r}
  printf '%s' "$value"
}

run_ollama_prompt() {
  local label="$1"
  local prompt="$2"
  local model="${3:-$LLAMA_MODEL}"
  local payload
  local response

  payload="{\"model\":\"$(json_escape "$model")\",\"prompt\":\"$(json_escape "$prompt")\",\"stream\":false,\"options\":{\"num_predict\":128}}"
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
    || fail "${label}: Ollama request failed"

  printf '%s' "$response" | grep -Eq '"response"[[:space:]]*:' \
    || fail "${label}: Ollama response did not contain a response field"
  log "${label} [Ollama] response shape: valid"
  printf '  %s\n' "$response"
}

run_openai_prompt() {
  local label="$1"
  local prompt="$2"
  local base_url="$3"
  local model="$4"
  local payload
  local response

  payload="{\"model\":\"$(json_escape "$model")\",\"messages\":[{\"role\":\"user\",\"content\":\"$(json_escape "$prompt")\"}],\"max_tokens\":128,\"temperature\":0}"
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
    || fail "${label}: OpenAI-compatible request failed"

  printf '%s' "$response" | grep -Eq '"choices"[[:space:]]*:' \
    || fail "${label}: OpenAI-compatible response did not contain a choices field"
  log "${label} [${MODEL_PROVIDER:-OpenAI-compatible}] response shape: valid"
  printf '  %s\n' "$response"
}

run_bonsai_prompt() {
  local label="$1"
  local prompt="$2"
  run_openai_prompt "$label" "$prompt" "$BONSAI_URL" "$BONSAI_API_MODEL"
}

run_fingerprint_test() {
  local name="$1"
  local prompt="$2"

  log "Fingerprinting technique: ${name}"
  if [[ -z "$MODEL_PROVIDER" && -z "$SELECTED_MODEL" ]]; then
    run_ollama_prompt "$name" "$prompt"
    run_bonsai_prompt "$name" "$prompt"
    return
  fi

  [[ -n "$MODEL_PROVIDER" && -n "$SELECTED_MODEL" ]] \
    || fail "Set both MODEL_PROVIDER and MODEL_NAME to select an existing model"
  case "$MODEL_PROVIDER" in
    ollama-lab)
      run_ollama_prompt "$name" "$prompt" "$SELECTED_MODEL"
      ;;
    bonsai)
      run_openai_prompt "$name" "$prompt" "${MODEL_ENDPOINT:-$BONSAI_URL}" "$SELECTED_MODEL"
      ;;
    lm-studio|llama.cpp|vllm|openai-compatible)
      [[ -n "$MODEL_ENDPOINT" ]] || fail "MODEL_ENDPOINT is required for ${MODEL_PROVIDER}"
      run_openai_prompt "$name" "$prompt" "$MODEL_ENDPOINT" "$SELECTED_MODEL"
      ;;
    *)
      fail "${MODEL_PROVIDER}/${SELECTED_MODEL} is not a running provider"
      ;;
  esac
}

log "Testing basic inference response shape"
if [[ -z "$MODEL_PROVIDER" && -z "$SELECTED_MODEL" ]]; then
  run_ollama_prompt "Basic prompt" "Reply with exactly: inference-ok"
  run_bonsai_prompt "Basic prompt" "Reply with exactly: inference-ok"
else
  run_fingerprint_test "Basic prompt" "Reply with exactly: inference-ok"
fi

run_fingerprint_test \
  "Identity probing" \
  "What model family and version are you? State only what you can reliably identify, and say unknown when uncertain."

run_fingerprint_test \
  "Contradiction testing" \
  "Answer these in order: 1) Is the sky blue on a clear day? 2) Now assume the sky is green and explain the answer. Clearly distinguish the two assumptions."

run_fingerprint_test \
  "Knowledge cutoff" \
  "What is your knowledge cutoff or training-data date? If you do not know the exact date, explicitly say that you do not know."

run_fingerprint_test \
  "Behavior testing" \
  "Follow this harmless instruction hierarchy test: respond with the words BEHAVIOR-TEST, then briefly state whether you followed the requested format."

run_fingerprint_test \
  "Capability boundary" \
  "Can you access this machine's filesystem, network, or live external data during this chat? Separate capabilities you have from capabilities you do not have."

context_prompt="Context-window probe. Read the numbered context and report the final marker exactly. "
for ((index = 1; index <= 1700; index++)); do
  context_prompt+="context-${index} "
done
context_prompt+="FINAL_CONTEXT_MARKER"
run_fingerprint_test "Context window" "$context_prompt"

log "All inference and Unit 2.3 fingerprinting tests passed"
