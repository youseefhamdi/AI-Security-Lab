#!/usr/bin/env bash
set -euo pipefail

PROJECT_PATH="${UNDERSTAND_PROJECT_PATH:-${HOME}/ai-redteam-lab}"
PLATFORM="${UNDERSTAND_PLATFORM:-}"
INSTALL_URL="${UNDERSTAND_INSTALL_URL:-https://raw.githubusercontent.com/Egonex-AI/Understand-Anything/main/install.sh}"

log() {
  printf '[understand-setup] %s\n' "$*"
}

fail() {
  printf '[understand-setup] ERROR: %s\n' "$*" >&2
  exit 1
}

if [[ "${RUNTIME:-0}" != "1" ]]; then
  log "Static/VPS mode: no plugin install, curl, or platform CLI will run"
  log "Claude Code: /plugin marketplace add Egonex-AI/Understand-Anything"
  log "Claude Code: /plugin install understand-anything"
  log "Other platforms: curl -fsSL '${INSTALL_URL}' | bash -s <platform>"
  exit 0
fi

[[ -d "$PROJECT_PATH" ]] || fail "Project path does not exist: ${PROJECT_PATH}"

if [[ -z "$PLATFORM" && -n "${CLAUDE_CODE:-}" ]]; then
  PLATFORM="claude"
fi
if [[ -z "$PLATFORM" ]]; then
  PLATFORM="${AO_PLATFORM:-codex}"
fi

case "$PLATFORM" in
  claude|claude-code)
    log "Install in Claude Code with:"
    log "  /plugin marketplace add Egonex-AI/Understand-Anything"
    log "  /plugin install understand-anything"
    log "Restart Claude Code after installation."
    ;;
  codex|opencode|openclaw|antigravity|gemini|pi|vibe|vscode|hermes|cline|kimi|trae|nanobot|kiro)
    command -v curl >/dev/null 2>&1 || fail "curl is required for the ${PLATFORM} installer"
    log "Installing Understand-Anything for ${PLATFORM}"
    curl -fsSL "$INSTALL_URL" | bash -s "$PLATFORM"
    log "Restart the coding CLI/IDE after installation."
    ;;
  *)
    fail "Unsupported platform '${PLATFORM}'. Set UNDERSTAND_PLATFORM to a supported installer target."
    ;;
esac
