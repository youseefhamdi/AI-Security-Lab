#!/usr/bin/env bash
# ------------------------------------------------------------------
# refresh_ui.sh — pull the latest UI and apply it to running containers.
#
# The UI HTML files (training-challenges, aurora, phoenix, assistant)
# are bind-mounted read-only into their containers, so after a git
# pull the new interface is live as soon as the containers are
# recreated — no docker build is needed.
# ------------------------------------------------------------------
set -euo pipefail
cd "$(dirname "$0")/.."

echo "[refresh-ui] git fetch + reset to origin/master"
git fetch origin
git reset --hard origin/master

echo "[refresh-ui] recreating UI containers (HTML is bind-mounted)"
docker compose up -d --force-recreate training-challenges aurora phoenix assistant

echo "[refresh-ui] verifying running containers serve the latest UI:"
check() {
    local name="$1" marker="$2" service="$3"
    if docker exec "$name" grep -q "$marker" /app/index.html 2>/dev/null; then
        echo "  [OK]  $name  -> new UI live (marker: '$marker')"
    else
        echo "  [!!]  $name  -> STILL OLD (marker missing). Rebuild:"
        echo "        docker compose build --no-cache $service && docker compose up -d $service"
    fi
}
check zodiac-bank-challenges "Virtual ledger" training-challenges
check lab-aurora "Aurora" aurora
check lab-phoenix "Phoenix" phoenix
check lab-assistant "Assistant" assistant

cat <<'EOF'

[refresh-ui] URLs (this repo's port map):
  Trainer    http://127.0.0.1:8060/   <- the CWL-style lab UI
  Aurora     http://127.0.0.1:5000/
  Phoenix    http://127.0.0.1:5001/
  Assistant  http://127.0.0.1:5002/
  Gate       http://127.0.0.1:5050/

NOTE: nothing in this repo is served on port 8060. If you were
opening :8060, that port belongs to a different container or a stale
tab — use the ports above. After opening, hard-refresh with
Ctrl+Shift+R to bypass browser cache.
EOF
