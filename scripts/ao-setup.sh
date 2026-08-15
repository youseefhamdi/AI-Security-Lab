#!/usr/bin/env bash
set -euo pipefail

PROJECT_PATH="${AO_PROJECT_PATH:-${HOME}/ai-redteam-lab}"
PROJECT_ID="${AO_PROJECT_ID:-ai-redteam-lab}"
INSTALL_DOCS_URL="${AO_INSTALL_DOCS_URL:-https://aoagents.dev/docs/installation/}"

log() {
  printf '[ao-setup] %s\n' "$*"
}

fail() {
  printf '[ao-setup] ERROR: %s\n' "$*" >&2
  exit 1
}

open_install_page() {
  case "$(uname -s 2>/dev/null || printf 'unknown')" in
    Darwin)
      command -v open >/dev/null 2>&1 && open "$INSTALL_DOCS_URL" || true
      ;;
    Linux)
      command -v xdg-open >/dev/null 2>&1 && xdg-open "$INSTALL_DOCS_URL" >/dev/null 2>&1 || true
      ;;
    MINGW*|MSYS*|CYGWIN*)
      command -v explorer.exe >/dev/null 2>&1 && explorer.exe "$INSTALL_DOCS_URL" || true
      ;;
    *)
      log "Open ${INSTALL_DOCS_URL} in a browser to install Agent Orchestrator"
      ;;
  esac
}

install_from_path() {
  local installer="$1"
  local platform
  platform="$(uname -s 2>/dev/null || printf 'unknown')"

  case "$platform" in
    Darwin)
      command -v open >/dev/null 2>&1 || fail "macOS 'open' command is required"
      open "$installer"
      ;;
    Linux)
      case "$installer" in
        *.deb)
          command -v apt-get >/dev/null 2>&1 || fail "apt-get is required for a .deb installer"
          sudo apt-get install -y "$installer"
          ;;
        *.rpm)
          command -v rpm >/dev/null 2>&1 || fail "rpm is required for an .rpm installer"
          sudo rpm -U "$installer"
          ;;
        *.AppImage|*.appimage)
          local target="${HOME}/.local/bin/agent-orchestrator"
          mkdir -p "$(dirname "$target")"
          cp "$installer" "$target"
          chmod +x "$target"
          log "Installed AppImage at ${target}"
          ;;
        *)
          fail "Unsupported Linux installer format: ${installer}"
          ;;
      esac
      ;;
    MINGW*|MSYS*|CYGWIN*)
      if [[ "$installer" == *.msi ]] && command -v msiexec.exe >/dev/null 2>&1; then
        msiexec.exe /i "$installer"
      elif command -v cmd.exe >/dev/null 2>&1; then
        cmd.exe /c start "" "$installer"
      else
        fail "Windows installer launcher is unavailable"
      fi
      ;;
    *)
      fail "Unsupported platform: ${platform}"
      ;;
  esac
}

register_project() {
  command -v ao >/dev/null 2>&1 || {
    log "The desktop app owns the daemon; register this repository in AO with:"
    log "  ao project add --path '${PROJECT_PATH}' --id '${PROJECT_ID}'"
    log "If the desktop app does not expose the CLI, use Add project in its sidebar."
    return 0
  }

  [[ -d "$PROJECT_PATH" ]] || fail "Project path does not exist: ${PROJECT_PATH}"
  ao project add --path "$PROJECT_PATH" --id "$PROJECT_ID" || {
    log "Project may already be registered; inspect it with: ao project get ${PROJECT_ID}"
  }
  log "Registered ${PROJECT_ID}; inspect daemon health with: ao status"
}

if [[ "${RUNTIME:-0}" != "1" ]]; then
  log "Static/VPS mode: no installer, daemon, package manager, or desktop app will run"
  log "On a local machine, set RUNTIME=1 and rerun this script"
  log "Installation guide: ${INSTALL_DOCS_URL}"
  exit 0
fi

log "Agent Orchestrator uses the desktop app as the canonical installer"
log "Supported platforms: macOS, Linux, and Windows"
open_install_page

if [[ -n "${AO_INSTALLER_PATH:-}" ]]; then
  [[ -f "$AO_INSTALLER_PATH" ]] || fail "AO_INSTALLER_PATH does not exist: ${AO_INSTALLER_PATH}"
  log "Installing from ${AO_INSTALLER_PATH}"
  install_from_path "$AO_INSTALLER_PATH"
else
  log "Install and open Agent Orchestrator from ${INSTALL_DOCS_URL}, then continue"
fi

register_project
log "AO should watch ${PROJECT_PATH}; worker/orchestrator roles are documented in orchestrator-config/project.yaml"
