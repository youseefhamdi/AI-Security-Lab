#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${ENV_FILE:-.env}"
HOST_URL="${LMSTUDIO_EMBEDDING_URL:-http://127.0.0.1:1234/v1}"
CONTAINER_URL="${LMSTUDIO_EMBEDDING_CONTAINER_URL:-http://host.docker.internal:1234/v1}"
MODEL="${1:-${LMSTUDIO_EMBEDDING_MODEL:-}}"
DIMENSION="${2:-${EMBEDDING_DIM:-}}"

log() {
  printf '[configure-lmstudio-embeddings] %s\n' "$*"
}

fail() {
  printf '[configure-lmstudio-embeddings] ERROR: %s\n' "$*" >&2
  exit 1
}

if [[ "${RUNTIME:-0}" != "1" ]]; then
  log "Static/VPS mode: no local .env changes will be made"
  log "Local execution: RUNTIME=1 ./scripts/configure_lmstudio_embeddings.sh MODEL_ID DIMENSION"
  exit 0
fi

command -v python3 >/dev/null 2>&1 || fail "RUNTIME=1 requires python3"
[[ -n "$MODEL" ]] || fail "Pass the exact LM Studio embedding model id as the first argument"
[[ "$MODEL" != *$'\n'* && "$MODEL" != *$'\r'* ]] || fail "model id cannot contain newlines"
[[ "$DIMENSION" =~ ^[1-9][0-9]*$ ]] || fail "Pass a positive embedding dimension as the second argument"

python3 - "$ENV_FILE" "$HOST_URL" "$CONTAINER_URL" "$MODEL" "$DIMENSION" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
updates = {
    "LMSTUDIO_EMBEDDING_MODEL": sys.argv[4],
    "EMBEDDING_BASE_URL": sys.argv[2],
    "EMBEDDING_CONTAINER_BASE_URL": sys.argv[3],
    "EMBEDDING_MODEL": sys.argv[4],
    "EMBEDDING_DIM": sys.argv[5],
    "LIGHTRAG_EMBEDDING_BINDING": "openai",
    "LIGHTRAG_EMBEDDING_HOST": sys.argv[3],
    "LIGHTRAG_EMBEDDING_MODEL": sys.argv[4],
    "LIGHTRAG_EMBEDDING_API_KEY": "local",
    "LIGHTRAG_EMBEDDING_DIM": sys.argv[5],
    "MEM0_EMBEDDER_PROVIDER": "openai",
    "MEM0_EMBEDDER_HOST": sys.argv[3],
    "MEM0_EMBEDDER_MODEL": sys.argv[4],
    "MEM0_EMBEDDER_API_KEY": "local",
}

existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
seen = set()
output = []
for line in existing:
    stripped = line.lstrip()
    if not stripped or stripped.startswith("#") or "=" not in line:
        output.append(line)
        continue
    key = line.split("=", 1)[0].strip()
    if key in updates:
        output.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        output.append(line)
for key, value in updates.items():
    if key not in seen:
        output.append(f"{key}={value}")
path.write_text("\n".join(output) + "\n", encoding="utf-8")
PY

log "Configured ${MODEL} (${DIMENSION} dimensions) for host and container LM Studio endpoints in ${ENV_FILE}"
log "Probe it with: RUNTIME=1 LMSTUDIO_EMBEDDING_MODEL=${MODEL} EMBEDDING_DIM=${DIMENSION} ./scripts/check_lmstudio_embeddings.sh"
