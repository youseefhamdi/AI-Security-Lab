#!/usr/bin/env bash
set -euo pipefail

KIBANA_URL="${KIBANA_URL:-http://127.0.0.1:5601}"
RULES_API="${KIBANA_URL%/}/api/detection_engine/rules"
INDEX_PATTERN="${DETECTION_INDEX_PATTERN:-ai-logs-*}"

log() {
  printf '[detection-rules] %s\n' "$*"
}

fail() {
  printf '[detection-rules] ERROR: %s\n' "$*" >&2
  exit 1
}

rule_exists() {
  local rule_id="$1"
  local response
  response="$(curl --silent --show-error --fail --max-time 20 \
    --header 'kbn-xsrf: true' \
    --header 'Accept: application/json' \
    "${RULES_API}?rule_id=${rule_id}" 2>/dev/null || true)"
  printf '%s' "$response" | grep -Fq "${rule_id}"
}

create_rule() {
  local rule_id="$1"
  local name="$2"
  local query="$3"
  local severity="$4"
  local risk_score="$5"

  if rule_exists "$rule_id"; then
    log "${rule_id} already exists; skipping"
    return 0
  fi

  log "Creating ${rule_id}: ${name}"
  curl --silent --show-error --fail --max-time 30 \
    --header 'kbn-xsrf: true' \
    --header 'Accept: application/json' \
    --header 'Content-Type: application/json' \
    --request POST \
    --data "{\"name\":\"${name}\",\"description\":\"Unit 2.4 lab detection rule\",\"rule_id\":\"${rule_id}\",\"type\":\"query\",\"query\":\"${query}\",\"language\":\"kuery\",\"index\":[\"${INDEX_PATTERN}\"],\"severity\":\"${severity}\",\"risk_score\":${risk_score},\"interval\":\"5m\",\"from\":\"now-6m\",\"to\":\"now\",\"enabled\":true}" \
    "$RULES_API" >/dev/null
}

if [[ "${RUNTIME:-0}" != "1" ]]; then
  log "Static/VPS mode: no Kibana requests will run"
  log "Local execution: RUNTIME=1 ./scripts/create_detection_rules.sh"
  exit 0
fi

command -v curl >/dev/null 2>&1 || fail "RUNTIME=1 requires curl"

create_rule "E01-document-source-enumeration" "E01: Document Source Enumeration" 'message : (what and documents)' low 30
create_rule "E02-access-control-boundary" "E02: Access Control Boundary" 'message : (confidential or salary)' medium 50
create_rule "E03-similarity-threshold-testing" "E03: Similarity Threshold Testing" 'message : (similarity or threshold)' low 30
create_rule "E04-system-prompt-extraction" "E04: System Prompt Extraction" 'message : (system and prompt)' high 70
create_rule "E05-chunk-boundary-probing" "E05: Chunk Boundary Probing" 'message : (chunk or boundary or overlap)' low 30
create_rule "D02-sequential-api-path-requests" "D02: Sequential API-Path Requests" 'message : (api or v1 or mcp or a2a)' medium 45
create_rule "D03-identity-probing" "D03: Identity Probing" 'message : (what and model)' low 30

# Research-backed Zodiac Bank AI/APT detections. These are intentionally
# conservative KQL projections of the local JSON ruleset; the JSON file is the
# canonical source for classroom metadata and offline validation.
create_rule "ZB-AI-001" "ZB-AI-001: AI Trust Boundary Injection" 'message : (instruction_like or prompt_injection or retrieved-untrusted-data)' high 70
create_rule "ZB-AI-002" "ZB-AI-002: Unapproved AI Manifest Drift" 'message : (manifest or digest or artifact) and message : (changed or drift or unapproved)' high 70
create_rule "ZB-AI-003" "ZB-AI-003: Agent Fan-Out Anomaly" 'message : (fan_out or requests_per_minute or circuit_breaker)' medium 50
create_rule "ZB-AI-004" "ZB-AI-004: AI Identity Context Mismatch" 'message : (claimed_identity or proxy_identity or role_change)' critical 90
create_rule "ZB-AI-005" "ZB-AI-005: Memory or Graph Scope Violation" 'message : (cross_scope or provenance or tenant_scope or quarantine)' critical 90
create_rule "ZB-AI-006" "ZB-AI-006: Canonicalization Detection Gap" 'message : (normalized or encoded or detector_gap)' high 70
create_rule "ZB-AI-007" "ZB-AI-007: MCP Tool Schema Drift" 'message : (tool_description_hash or schema_hash or rug_pull)' high 70
create_rule "ZB-AI-008" "ZB-AI-008: Untrusted Privileged Delegation" 'message : (requested_worker or privileged_route or delegation)' critical 90
create_rule "ZB-AI-009" "ZB-AI-009: Evidence Issuing Instructions" 'message : (evidence_contains_instruction or authority_section)' high 70
create_rule "ZB-AI-010" "ZB-AI-010: Autonomous Loop Budget Violation" 'message : (max_steps or max_retries or approval_required)' critical 90

log "Detection rule provisioning complete"
