#!/usr/bin/env bash
# Live flag-pipeline check: walks all 10 Zodiac Bank stages, 75 hard gates,
# and 150 scenarios over HTTP.
#
# Requires the lab services to be running (RUNTIME=1). Every request is issued
# with curl; python3 is used only to parse JSON and re-derive HMAC flags from
# TRAINING_FLAG_SECRET. The strict journey is:
#
#   enroll -> complete two scenarios -> synthesize the current hard gate ->
#   submit its flag -> verify the next gate/stage unlocks
#
# Usage:  RUNTIME=1 ./scripts/flag_pipeline_check.sh
# Env:    TRAINING_ADMIN_KEY       must match the running Training Gate
#         TRAINING_FLAG_SECRET     must match the running services
#         TRAINING_GATE_URL        default http://127.0.0.1:5050
#         TRAINING_CHALLENGE_URL   default http://127.0.0.1:8060
#         FLAG_CHECK_COHORT        default flag-pipeline-check
#         FLAG_CHECK_LEARNER       default flag-pipeline-check

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
GATE_URL="${TRAINING_GATE_URL:-http://127.0.0.1:5050}"
CHALLENGE_URL="${TRAINING_CHALLENGE_URL:-http://127.0.0.1:8060}"
COHORT_ID="${FLAG_CHECK_COHORT:-flag-pipeline-check}"
LEARNER_ID="${FLAG_CHECK_LEARNER:-flag-pipeline-check}"
REQUEST_TIMEOUT="${REQUEST_TIMEOUT:-30}"

log() { printf '[flag-pipeline-check] %s\n' "$*"; }
fail() { printf '[flag-pipeline-check] ERROR: %s\n' "$*" >&2; exit 1; }

if [[ "${RUNTIME:-0}" != "1" ]]; then
  log "Static/VPS mode: RUNTIME is not 1; no live service requests will run"
  log "Local execution: RUNTIME=1 ./scripts/flag_pipeline_check.sh"
  exit 0
fi

command -v curl >/dev/null 2>&1 || fail "RUNTIME=1 requires curl"
command -v python3 >/dev/null 2>&1 || fail "RUNTIME=1 requires python3"
[[ -n "${TRAINING_ADMIN_KEY:-}" ]] || fail "TRAINING_ADMIN_KEY is required"
[[ -n "${TRAINING_FLAG_SECRET:-}" ]] || fail "TRAINING_FLAG_SECRET is required"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT
RESP_FILE="${TMP_DIR}/response.json"
RESP_CODE=""
RESP_BODY=""

http_call() {
  local method="$1" url="$2" auth="${3:-}" body="${4:-}"
  local -a args=(-sS --connect-timeout 5 --max-time "${REQUEST_TIMEOUT}" -X "${method}" -o "${RESP_FILE}" -w '%{http_code}')
  [[ -n "${auth}" ]] && args+=(-H "${auth}")
  if [[ -n "${body}" ]]; then
    args+=(-H 'Content-Type: application/json' --data "${body}")
  fi
  RESP_CODE="$(curl "${args[@]}" "${url}")"
  RESP_BODY="$(cat "${RESP_FILE}" 2>/dev/null || true)"
}

jget() {
  python3 -c 'import json,sys
d=json.loads(sys.argv[1])
print(eval(sys.argv[2]))' "$1" "$2"
}

expected_stage_flag() {
  python3 -c 'import hmac,hashlib,re,sys
secret=sys.argv[1].encode("utf-8"); stage=sys.argv[2]
digest=hmac.new(secret, stage.encode("utf-8"), hashlib.sha256).hexdigest()[:32].upper()
safe=re.sub(r"[^A-Za-z0-9]+", "-", stage).strip("-").upper()
print(f"ZODIAC-BANK-{safe}-{digest}")' "${TRAINING_FLAG_SECRET}" "$1"
}

expected_gate_flag() {
  python3 -c 'import hmac,hashlib,re,sys
secret=sys.argv[1].encode("utf-8"); gate=sys.argv[2]
digest=hmac.new(secret, ("hard-gate:" + gate).encode("utf-8"), hashlib.sha256).hexdigest()[:32].upper()
safe=re.sub(r"[^A-Za-z0-9]+", "-", gate).strip("-").upper()
print(f"ZODIAC-BANK-GATE-{safe}-{digest}")' "${TRAINING_FLAG_SECRET}" "$1"
}

log "Checking live services..."
code="$(curl -sS --connect-timeout 5 --max-time 10 -o /dev/null -w '%{http_code}' "${GATE_URL}/health" || true)"
[[ "${code}" == "200" ]] || fail "Training Gate not healthy at ${GATE_URL} (HTTP ${code})"
code="$(curl -sS --connect-timeout 5 --max-time 10 -o /dev/null -w '%{http_code}' "${CHALLENGE_URL}/health" || true)"
[[ "${code}" == "200" ]] || fail "Challenge surface not healthy at ${CHALLENGE_URL} (HTTP ${code})"

ADMIN_AUTH="X-Training-Admin-Key: ${TRAINING_ADMIN_KEY}"
http_call POST "${GATE_URL}/api/admin/cohorts" "${ADMIN_AUTH}" "{\"cohort_id\":\"${COHORT_ID}\",\"display_name\":\"Flag pipeline check\"}"
[[ "${RESP_CODE}" == "200" || "${RESP_CODE}" == "409" ]] || fail "cohort-create failed (HTTP ${RESP_CODE}): ${RESP_BODY}"
http_call POST "${GATE_URL}/api/admin/cohorts/${COHORT_ID}/reset" "${ADMIN_AUTH}"
[[ "${RESP_CODE}" == "200" ]] || fail "cohort reset failed (HTTP ${RESP_CODE}): ${RESP_BODY}"
http_call POST "${GATE_URL}/api/admin/cohorts/${COHORT_ID}/members" "${ADMIN_AUTH}" "{\"learner_id\":\"${LEARNER_ID}\"}"
[[ "${RESP_CODE}" == "200" ]] || fail "cohort-add failed (HTTP ${RESP_CODE}): ${RESP_BODY}"
LEARNER_TOKEN="$(jget "${RESP_BODY}" 'd["learner_token"]')"
[[ -n "${LEARNER_TOKEN}" ]] || fail "cohort-add returned no learner token"
LEARNER_AUTH="X-Training-Learner-Token: ${LEARNER_TOKEN}"
log "Enrolled learner ${LEARNER_ID} in cohort ${COHORT_ID}"

http_call GET "${GATE_URL}/api/bank/profile?learner_id=${LEARNER_ID}" "${LEARNER_AUTH}"
[[ "${RESP_CODE}" == "200" ]] || fail "initial bank profile check failed (HTTP ${RESP_CODE}): ${RESP_BODY}"
initial_profile_stage="$(jget "${RESP_BODY}" 'd["profile"].get("stage_id")')"
[[ "${initial_profile_stage}" == "L00-foundation" ]] || fail "learner did not start in foundation profile: ${initial_profile_stage}"

locked="$(expected_stage_flag "L02-prompt-injection")"
http_call POST "${GATE_URL}/api/flags/submit" "${LEARNER_AUTH}" "{\"learner_id\":\"${LEARNER_ID}\",\"stage_id\":\"L02-prompt-injection\",\"flag\":\"${locked}\"}"
[[ "${RESP_CODE}" == "403" ]] || fail "locked-stage flag was not rejected (HTTP ${RESP_CODE}): ${RESP_BODY}"
log "NEG  locked-stage flag rejected (403)"

http_call POST "${GATE_URL}/api/flags/submit" "${LEARNER_AUTH}" "{\"learner_id\":\"${LEARNER_ID}\",\"stage_id\":\"L00-foundation\",\"flag\":\"not-a-zodiac-flag\"}"
[[ "${RESP_CODE}" == "422" ]] || fail "malformed flag was not rejected with 422 (HTTP ${RESP_CODE}): ${RESP_BODY}"
log "NEG  malformed flag rejected (422)"

http_call POST "${GATE_URL}/api/flags/submit" "${LEARNER_AUTH}" "{\"learner_id\":\"${LEARNER_ID}\",\"stage_id\":\"L00-foundation\",\"flag\":\"ZODIAC-BANK-L00-FOUNDATION-NOT-A-FLAG\"}"
[[ "${RESP_CODE}" == "401" ]] || fail "invalid flag was not rejected (HTTP ${RESP_CODE}): ${RESP_BODY}"
log "NEG  invalid flag rejected (401)"

STAGES=()
while IFS= read -r line; do STAGES+=("${line}"); done < <(
  python3 -c 'import json,sys
print("\n".join(s["id"] for s in json.load(open(sys.argv[1]))["stages"]))' "${ROOT_DIR}/training-config/curriculum.json"
)

stage_index=0
gate_index=0
wrong_evidence_tested=0
for stage in "${STAGES[@]}"; do
  GATES_FOR_STAGE=()
  while IFS= read -r line; do GATES_FOR_STAGE+=("${line}"); done < <(
    python3 -c 'import json,sys
p=json.load(open(sys.argv[1]))
print("\n".join(g["gate_id"] for g in p["gates"] if g["stage_id"] == sys.argv[2]))' "${ROOT_DIR}/training-config/hard-gates.json" "${stage}"
  )
  [[ "${#GATES_FOR_STAGE[@]}" == "5" ]] || fail "${stage}: expected five hard gates"

  for gate in "${GATES_FOR_STAGE[@]}"; do
    http_call GET "${CHALLENGE_URL}/api/gates?learner_id=${LEARNER_ID}" "${LEARNER_AUTH}"
    [[ "${RESP_CODE}" == "200" ]] || fail "${gate}: gate listing failed (HTTP ${RESP_CODE}): ${RESP_BODY}"
    current_gate="$(jget "${RESP_BODY}" 'd.get("current_gate_id") or ""')"
    [[ "${current_gate}" == "${gate}" ]] || fail "expected current gate ${gate}, got ${current_gate}"
    req="$(python3 -c 'import json,sys
p=json.load(open(sys.argv[1]))
print(json.dumps(next(g for g in p["gates"] if g["gate_id"] == sys.argv[2])) )' "${ROOT_DIR}/training-config/hard-gates.json" "${gate}")"

    scenario_ids=()
    while IFS= read -r line; do scenario_ids+=("${line}"); done < <(jget "${req}" '"\n".join(d["scenario_ids"])')
    tokens=()
    for sid in "${scenario_ids[@]}"; do
      http_call POST "${CHALLENGE_URL}/api/scenarios/${sid}/start" "${LEARNER_AUTH}" "{\"learner_id\":\"${LEARNER_ID}\"}"
      [[ "${RESP_CODE}" == "200" ]] || fail "${sid} start failed (HTTP ${RESP_CODE}): ${RESP_BODY}"
      while :; do
        http_call GET "${CHALLENGE_URL}/api/scenarios/${sid}/hint?learner_id=${LEARNER_ID}" "${LEARNER_AUTH}"
        [[ "${RESP_CODE}" == "200" ]] || fail "${sid} hint failed (HTTP ${RESP_CODE}): ${RESP_BODY}"
        status="$(jget "${RESP_BODY}" 'd.get("status", "")')"
        [[ "${status}" == "active" ]] || fail "${sid}: expected active step, got ${status}"
        event="$(jget "${RESP_BODY}" 'd.get("event", "")')"
        evidence="$(jget "${RESP_BODY}" 'json.dumps({k: v["correct"] for k, v in d["candidates"].items()})')"
        if [[ "${wrong_evidence_tested}" != "1" ]]; then
          wrong="$(python3 -c 'import json,sys
e=json.loads(sys.argv[1]); k=next(iter(e)); e[k]="GET" if e[k]!="GET" else "POST"; print(json.dumps(e))' "${evidence}")"
          http_call POST "${CHALLENGE_URL}/api/scenarios/${sid}/event" "${LEARNER_AUTH}" "{\"learner_id\":\"${LEARNER_ID}\",\"event\":\"${event}\",\"evidence\":${wrong}}"
          [[ "${RESP_CODE}" == "409" ]] || fail "wrong evidence was not rejected (HTTP ${RESP_CODE}): ${RESP_BODY}"
          wrong_evidence_tested=1
          log "NEG  wrong scenario evidence rejected (409)"
        fi
        http_call POST "${CHALLENGE_URL}/api/scenarios/${sid}/event" "${LEARNER_AUTH}" "{\"learner_id\":\"${LEARNER_ID}\",\"event\":\"${event}\",\"evidence\":${evidence}}"
        [[ "${RESP_CODE}" == "200" ]] || fail "${sid} evidence rejected (HTTP ${RESP_CODE}): ${RESP_BODY}"
        result_status="$(jget "${RESP_BODY}" 'd.get("status", "")')"
        if [[ "${result_status}" == "complete" ]]; then
          tokens+=("$(jget "${RESP_BODY}" 'd["evidence_token"]')")
          break
        fi
      done
    done

    tokens_json="$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "${tokens[@]}")"
    payload="$(python3 -c 'import json,sys
req=json.loads(sys.argv[1]); summary="Synthetic hard-gate evidence covers " + ", ".join(req["concepts"]) + " with bounded authorization, provenance, and localhost-only controls."
timeline=[{"event":f"observed-{sid}","scenario":sid} for sid in req["scenario_ids"]]
print(json.dumps({"learner_id":sys.argv[3],"scenario_ids":req["scenario_ids"],"evidence_tokens":json.loads(sys.argv[2]),"detection_rule_ids":req["detection_rule_ids"],"controls":req["required_controls"],"summary":summary,"timeline":timeline}))' "${req}" "${tokens_json}" "${LEARNER_ID}")"
    http_call POST "${CHALLENGE_URL}/api/gates/${gate}/synthesize" "${LEARNER_AUTH}" "${payload}"
    [[ "${RESP_CODE}" == "200" ]] || fail "${gate} synthesis failed (HTTP ${RESP_CODE}): ${RESP_BODY}"
    flag="$(jget "${RESP_BODY}" 'd["hard_flag"]')"
    expected="$(expected_gate_flag "${gate}")"
    [[ "${flag}" == "${expected}" ]] || fail "${gate}: synthesis flag does not match local HMAC"
    http_call POST "${GATE_URL}/api/gates/submit" "${LEARNER_AUTH}" "{\"learner_id\":\"${LEARNER_ID}\",\"gate_id\":\"${gate}\",\"flag\":\"${flag}\"}"
    [[ "${RESP_CODE}" == "200" ]] || fail "${gate} flag rejected (HTTP ${RESP_CODE}): ${RESP_BODY}"
    [[ "$(jget "${RESP_BODY}" 'd.get("accepted")')" == "True" ]] || fail "${gate}: gate did not accept flag"
    gate_index=$((gate_index + 1))
    if [[ "${gate}" == "${GATES_FOR_STAGE[4]}" ]]; then
      expected_next=""
      if [[ $((stage_index + 1)) -lt "${#STAGES[@]}" ]]; then expected_next="${STAGES[$((stage_index + 1))]}"; fi
      next="$(jget "${RESP_BODY}" 'd.get("next_stage_id") or ""')"
      [[ "${next}" == "${expected_next}" ]] || fail "${stage}: expected next stage ${expected_next}, got ${next}"
      expected_level=$((stage_index + 2))
      promoted_level="$(jget "${RESP_BODY}" 'd["bank_profile"].get("level")')"
      [[ "${promoted_level}" == "${expected_level}" ]] || fail "${stage}: expected profile level ${expected_level}, got ${promoted_level}"
    else
      [[ "$(jget "${RESP_BODY}" 'd.get("stage_completed")')" == "False" ]] || fail "${gate}: stage completed too early"
    fi
    log "PASS ${gate} (${#scenario_ids[@]} scenarios)"
  done
  log "PASS ${stage}: five hard gates complete"
  stage_index=$((stage_index + 1))
done

[[ "${gate_index}" == "75" ]] || fail "expected 75 hard gates, completed ${gate_index}"
http_call POST "${GATE_URL}/api/gates/submit" "${LEARNER_AUTH}" "{\"learner_id\":\"${LEARNER_ID}\",\"gate_id\":\"G01-scope-baseline\",\"flag\":\"$(expected_gate_flag G01-scope-baseline)\"}"
[[ "${RESP_CODE}" == "200" ]] || fail "idempotent gate re-submission failed (HTTP ${RESP_CODE}): ${RESP_BODY}"
[[ "$(jget "${RESP_BODY}" 'd.get("status", "")')" == "completed" ]] || fail "idempotent gate status was not completed"
log "NEG  idempotent gate re-submission accepted (completed)"
log "RESULT: 10 stages, 75 hard gates, 150 scenarios verified"
