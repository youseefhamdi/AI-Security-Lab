#!/usr/bin/env bash
set -euo pipefail

PROJECT_PATH="${UNDERSTAND_PROJECT_PATH:-${HOME}/ai-redteam-lab}"
GRAPH_PATH="${PROJECT_PATH}/.ua/knowledge-graph.json"
CLAUDE_BIN="${CLAUDE_BIN:-claude}"

log() {
  printf '[knowledge-graph] %s\n' "$*"
}

fail() {
  printf '[knowledge-graph] ERROR: %s\n' "$*" >&2
  exit 1
}

if [[ "${RUNTIME:-0}" != "1" ]]; then
  log "Static/VPS mode: no Understand-Anything CLI, model, or dashboard will run"
  log "Local commands: /understand and /understand-dashboard"
  exit 0
fi

[[ -d "$PROJECT_PATH" ]] || fail "Project path does not exist: ${PROJECT_PATH}"
command -v "$CLAUDE_BIN" >/dev/null 2>&1 || fail "${CLAUDE_BIN} is required to run /understand"

log "Running /understand on ${PROJECT_PATH}"
(
  cd "$PROJECT_PATH"
  "$CLAUDE_BIN" -p "/understand" --output-format text
)

[[ -s "$GRAPH_PATH" ]] || fail "Understand-Anything did not create ${GRAPH_PATH}"
log "Knowledge graph created: ${GRAPH_PATH}"

log "Launching /understand-dashboard"
(
  cd "$PROJECT_PATH"
  "$CLAUDE_BIN" -p "/understand-dashboard" --output-format text
)
