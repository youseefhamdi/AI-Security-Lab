#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
KONG_ADMIN_URL="${KONG_ADMIN_URL:-http://127.0.0.1:8001}"

log() {
  printf '[configure-kong] %s\n' "$*"
}

fail() {
  printf '[configure-kong] ERROR: %s\n' "$*" >&2
  exit 1
}

request_status() {
  local method="$1"
  local url="$2"

  curl \
    --silent \
    --show-error \
    --request "$method" \
    --connect-timeout 5 \
    --max-time 15 \
    --header 'Accept: application/json' \
    --header 'Content-Type: application/json' \
    --output /dev/null \
    --write-out '%{http_code}' \
    "$url" || printf '000'
}

post_json() {
  local url="$1"
  local payload="$2"

  curl \
    --silent \
    --show-error \
    --fail \
    --request POST \
    --connect-timeout 5 \
    --max-time 15 \
    --header 'Accept: application/json' \
    --header 'Content-Type: application/json' \
    --data "$payload" \
    "$url" >/dev/null
}

ensure_service() {
  local name="$1"
  local upstream="$2"
  local url="${KONG_ADMIN_URL}/services/${name}"
  local status

  status="$(request_status GET "$url")"
  case "$status" in
    200)
      log "Service '${name}' already exists; skipping creation"
      ;;
    404)
      log "Creating service '${name}' -> ${upstream}"
      post_json "${KONG_ADMIN_URL}/services" \
        "{\"name\":\"${name}\",\"url\":\"${upstream}\"}" \
        || fail "Could not create service '${name}'"
      ;;
    *)
      fail "Could not check service '${name}' (HTTP ${status})"
      ;;
  esac
}

ensure_route() {
  local service_name="$1"
  local route_name="$2"
  local path_prefix="$3"
  local url="${KONG_ADMIN_URL}/routes/${route_name}"
  local status

  status="$(request_status GET "$url")"
  case "$status" in
    200)
      log "Route '${route_name}' already exists; skipping creation"
      ;;
    404)
      log "Creating route '${route_name}' for ${service_name} (${path_prefix}/*; strip_path=true)"
      post_json "${KONG_ADMIN_URL}/services/${service_name}/routes" \
        "{\"name\":\"${route_name}\",\"paths\":[\"${path_prefix}\"],\"strip_path\":true}" \
        || fail "Could not create route '${route_name}'"
      ;;
    *)
      fail "Could not check route '${route_name}' (HTTP ${status})"
      ;;
  esac
}

route_plugin_exists() {
  local route_name="$1"
  local response

  if ! response="$(curl \
    --silent \
    --show-error \
    --fail \
    --request GET \
    --connect-timeout 5 \
    --max-time 15 \
    --header 'Accept: application/json' \
    --header 'Content-Type: application/json' \
    "${KONG_ADMIN_URL}/routes/${route_name}/plugins")"; then
    fail "Could not inspect plugins for route '${route_name}'"
  fi

  printf '%s' "$response" | grep -Eq '"name"[[:space:]]*:[[:space:]]*"rate-limiting"'
}

ensure_assistant_rate_limit() {
  local route_name="assistant-route"

  if route_plugin_exists "$route_name"; then
    log "Rate-limiting plugin already exists on '${route_name}'; skipping creation"
    return
  fi

  log "Adding rate-limiting plugin to '${route_name}' (5 requests/second)"
  post_json "${KONG_ADMIN_URL}/routes/${route_name}/plugins" \
    '{"name":"rate-limiting","config":{"second":5,"policy":"local"}}' \
    || fail "Could not add rate-limiting plugin to '${route_name}'"
}

plugin_exists() {
  local plugin_name="$1"
  local response

  if ! response="$(curl \
    --silent \
    --show-error \
    --fail \
    --request GET \
    --connect-timeout 5 \
    --max-time 15 \
    --header 'Accept: application/json' \
    --header 'Content-Type: application/json' \
    "${KONG_ADMIN_URL}/plugins?name=${plugin_name}")"; then
    fail "Could not inspect global plugins"
  fi

  printf '%s' "$response" | grep -Eq '"name"[[:space:]]*:[[:space:]]*"'"${plugin_name}"'"'
}

ensure_global_cors() {
  if plugin_exists "cors"; then
    log "Global CORS plugin already exists; skipping creation"
    return
  fi

  log "Adding global CORS plugin"
  post_json "${KONG_ADMIN_URL}/plugins" \
    '{"name":"cors","config":{"origins":["*"],"methods":["GET","POST","PUT","PATCH","DELETE","OPTIONS"],"headers":["Accept","Authorization","Content-Type"],"credentials":false}}' \
    || fail "Could not add global CORS plugin"
}

log "Waiting for Kong Admin API"
KONG_ADMIN_URL="$KONG_ADMIN_URL" "${SCRIPT_DIR}/wait-for-kong.sh"

log "Configuring services and routes"
ensure_service "aurora" "http://aurora:5000"
ensure_route "aurora" "aurora-route" "/aurora"

ensure_service "phoenix" "http://phoenix:5000"
ensure_route "phoenix" "phoenix-route" "/phoenix"

ensure_service "assistant" "http://assistant:5000"
ensure_route "assistant" "assistant-route" "/assistant"

ensure_service "mcp" "http://mcp-wrapper:3000"
ensure_route "mcp" "mcp-route" "/mcp"

ensure_service "a2a" "http://a2a-router:5000"
ensure_route "a2a" "a2a-route" "/a2a"

ensure_service "lightrag" "http://lightrag:9621"
ensure_route "lightrag" "lightrag-route" "/lightrag"

ensure_service "mem0" "http://mem0:8000"
ensure_route "mem0" "mem0-route" "/mem0"

ensure_assistant_rate_limit
ensure_global_cors

log "Kong configuration complete"
