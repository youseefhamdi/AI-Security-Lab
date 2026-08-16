#!/usr/bin/env bash
# Bootstrap strong, persistent local secrets for Zodiac Bank strict security mode.
#
# The strict-mode services (training-gate, training-challenges, zodiac-context,
# mcp-wrapper) refuse to start when their Compose defaults are still the
# placeholder values. This helper generates cryptographically strong values once
# and stores them in <repo-root>/.env (gitignored, mode 600) so that:
#
#   1. `start_all.sh` (and a plain `docker compose up`) work out of the box.
#   2. TRAINING_FLAG_SECRET stays stable across restarts. Regenerating it would
#      invalidate every already-issued hard-gate flag.
#
# Existing non-placeholder values are always preserved.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"

gen_hex() {
  python3 -c "import secrets,sys; print(secrets.token_hex(int(sys.argv[1])))" "$1"
}

gen_token() {
  python3 -c "import secrets,sys; print(secrets.token_urlsafe(int(sys.argv[1])))" "$1"
}

# Read the current value of a single KEY=VALUE entry (empty when absent).
value_for() {
  local key="$1"
  local existing=""
  if [[ -f "$ENV_FILE" ]]; then
    existing="$(sed -nE "s|^${key}=(.+)$|\1|p" "$ENV_FILE" | tail -n 1)"
  fi
  printf '%s' "$existing"
}

# Ensure <key> holds a strong, non-placeholder value, generating it if needed.
ensure_secret() {
  local key="$1" placeholder="$2" value=""
  shift 2
  value="$(value_for "$key")"
  if [[ -z "$value" || "$value" == "$placeholder" ]]; then
    value="$("$@")"
    if [[ -f "$ENV_FILE" ]] && grep -qE "^${key}=" "$ENV_FILE"; then
      sed -i.bak -E "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
      rm -f "$ENV_FILE.bak"
    else
      printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
    fi
    printf '[bootstrap-secrets] generated %s\n' "$key"
  fi
}

if [[ ! -f "$ENV_FILE" ]]; then
  : > "$ENV_FILE"
fi

ensure_secret TRAINING_FLAG_SECRET "zodiac-bank-change-this-training-secret" gen_hex 32
ensure_secret TRAINING_ADMIN_KEY "zodiac-bank-admin-change-me" gen_token 24
ensure_secret GRAPH_CONTEXT_API_KEY "zodiac-bank-context-change-me" gen_token 32
ensure_secret ZODIAC_AGENT_SIGNING_KEY "zodiac-bank-agent-signing-key-change-me" gen_token 32

chmod 600 "$ENV_FILE"
printf '[bootstrap-secrets] secrets ready in %s\n' "$ENV_FILE"
