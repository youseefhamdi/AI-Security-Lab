#!/usr/bin/env bash
# Live flag-pipeline check: walks all 10 Zodiac Bank stages over HTTP.
#
# Requires the lab services to be running (RUNTIME=1). Every request is issued
# with curl; python3 is used only to parse JSON and to re-derive the expected
# HMAC flags from TRAINING_FLAG_SECRET (the same secret the running services
# use). The journey mirrors scripts/zodiac_bank_progression_test.py, but over
# the real HTTP surface:
#
#   enroll -> complete each required scenario -> synthesize the stage ->
#   submit the hard flag to the gate -> verify the exact next stage unlocks
#
# plus live negative checks: locked-stage flag (403), invalid flag (401),
# wrong scenario evidence (409), and idempotent re-submission.
#
# Usage:  RUNTIME=1 ./scripts/flag_pipeline_check.sh
# Env:    TRAINING_ADMIN_KEY  (must match the running Training Gate)
#         TRAINING_FLAG_SECRET (must match the running services)
#         TRAINING_GATE_URL     default http://127.0.0.1:5050
#         TRAINING_CHALLENGE_URL default http://127.0.0.1:5060
#         FLAG_CHECK_COHORT      default flag-pipeline-check
#         FLAG_CHECK_LEARNER     default flag-pipeline-check

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
GATE_URL="${TRAINING_GATE_URL:-http://127.0.0.1:5050}"
CHALLENGE_URL="${TRAINING_CHALLENGE_URL:-http://127.0.0.1:5060}"
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
[[ -n "${TRAINING_ADMIN_KEY:-}" ]] || fail "TRAINING_ADMIN_KEY is required and must match the running Training Gate"
[[ -n "${TRAINING_FLAG_SECRET:-}" ]] || fail "TRAINING_FLAG_SECRET is required and must match the running services"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT
RESP_FILE="${TMP_DIR}/response.json"
RESP_CODE=""
RESP_BODY=""

# http_call <method> <url> [<auth-header>] [<json-body>]
# Sets RESP_CODE (HTTP status) and RESP_BODY (response body).
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

# jget <json> <python-expression-on-d> — prints the evaluated result.
jget() {
  python3 -c 'import json,sys
d=json.loads(sys.argv[1])
print(eval(sys.argv[2]))' "$1" "$2"
}

# expected_flag <stage_id> — re-derives the HMAC flag from TRAINING_FLAG_SECRET.
expected_flag() {
  python3 -c 'import hmac,hashlib,re,sys
secret=sys.argv[1].encode("utf-8"); stage=sys.argv[2]
digest=hmac.new(secret, stage.encode("utf-8"), hashlib.sha256).hexdigest()[:32].upper()
safe=re.sub(r"[^A-Za-z0-9]+", "-", stage).strip("-").upper()
print(f"ZODIAC-BANK-{safe}-{digest}")' "${TRAINING_FLAG_SECRET}" "$1"
}

# --- 1. health checks ------------------------------------------------------
log "Checking live services..."
code="$(curl -sS --connect-timeout 5 --max-time 10 -o /dev/null -w '%{http_code}' "${GATE_URL}/health" || true)"
[[ "${code}" == "200" ]] || fail "Training Gate not healthy at ${GATE_URL} (HTTP ${code}); start the lab first"
code="$(curl -sS --connect-timeout 5 --max-time 10 -o /dev/null -w '%{http_code}' "${CHALLENGE_URL}/health" || true)"
[[ "${code}" == "200" ]] || fail "Challenge surface not healthy at ${CHALLENGE_URL} (HTTP ${code}); start the lab first"

# --- 2. enroll -------------------------------------------------------------
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
[[ "${initial_profile_stage}" == "L00-foundation" ]] || fail "learner did not start in foundation bank profile: ${initial_profile_stage}"
log "BANK profile promoted dynamically: initial posture is foundation-observe"

# --- 3. negative checks before the walk (learner is on L00) ----------------
locked="$(expected_flag "L02-prompt-injection")"
http_call POST "${GATE_URL}/api/flags/submit" "${LEARNER_AUTH}" "{\"learner_id\":\"${LEARNER_ID}\",\"stage_id\":\"L02-prompt-injection\",\"flag\":\"${locked}\"}"
[[ "${RESP_CODE}" == "403" ]] || fail "locked-stage flag was not rejected (HTTP ${RESP_CODE}): ${RESP_BODY}"
log "NEG  locked-stage flag rejected (403)"

http_call POST "${GATE_URL}/api/flags/submit" "${LEARNER_AUTH}" "{\"learner_id\":\"${LEARNER_ID}\",\"stage_id\":\"L00-foundation\",\"flag\":\"ZODIAC-BANK-L00-FOUNDATION-NOT-A-FLAG\"}"
[[ "${RESP_CODE}" == "401" ]] || fail "invalid flag was not rejected (HTTP ${RESP_CODE}): ${RESP_BODY}"
log "NEG  invalid flag rejected (401)"

# --- 4. walk all 10 stages --------------------------------------------------
STAGES=()
while IFS= read -r line; do STAGES+=("${line}"); done < <(
  python3 -c 'import json,sys
print("\n".join(s["id"] for s in json.load(open(sys.argv[1]))["stages"]))' "${ROOT_DIR}/training-config/curriculum.json"
)

stage_index=0
for stage in "${STAGES[@]}"; do
  req="$(python3 -c 'import json,sys
d=json.load(open(sys.argv[1]))["stage_requirements"]
print(json.dumps(d[sys.argv[2]]))' "${ROOT_DIR}/training-config/scenarios.json" "${stage}")"

  scenario_ids=()
  while IFS= read -r line; do scenario_ids+=("${line}"); done < <(jget "${req}" '"\n".join(d["scenario_ids"])')

  tokens=()
  wrong_evidence_tested=0
  for sid in "${scenario_ids[@]}"; do
    http_call POST "${CHALLENGE_URL}/api/scenarios/${sid}/start" "${LEARNER_AUTH}" "{\"learner_id\":\"${LEARNER_ID}\"}"
    [[ "${RESP_CODE}" == "200" ]] || fail "${sid} start failed (HTTP ${RESP_CODE}): ${RESP_BODY}"

    step_count=0
    while :; do
      http_call GET "${CHALLENGE_URL}/api/scenarios/${sid}/hint?learner_id=${LEARNER_ID}" "${LEARNER_AUTH}"
      [[ "${RESP_CODE}" == "200" ]] || fail "${sid} hint failed (HTTP ${RESP_CODE}): ${RESP_BODY}"
      status="$(jget "${RESP_BODY}" 'd.get("status", "")')"
      [[ "${status}" == "active" ]] || fail "${sid}: expected an active step, got ${status}"
      event="$(jget "${RESP_BODY}" 'd.get("event", "")')"
      evidence="$(jget "${RESP_BODY}" 'json.dumps({k: v["correct"] for k, v in d["candidates"].items()})')"

      if [[ "${wrong_evidence_tested}" != "1" ]]; then
        wrong="$(python3 -c 'import json,sys
e=json.loads(sys.argv[1])
k=next(iter(e))
e[k] = "GET" if e[k] != "GET" else "POST"
print(json.dumps(e))' "${evidence}")"
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
      step_count=$((step_count + 1))
      [[ "${step_count}" -le 20 ]] || fail "${sid} did not complete within 20 steps"
    done
  done

  tokens_json="$(python3 -c 'import json,sys
print(json.dumps(sys.argv[1:]))' "${tokens[@]}")"
  payload="$(python3 -c 'import json,sys
req=json.loads(sys.argv[1])
summary="Synthetic incident summary: " + ", ".join(req["concepts"]) + " observed with approval gates, provenance, and a complete timeline; all activity confined to the localhost scope."
timeline=[{"event": f"observed-{sid}", "scenario": sid} for sid in req["scenario_ids"]]
print(json.dumps({"learner_id": sys.argv[3], "scenario_ids": req["scenario_ids"], "evidence_tokens": json.loads(sys.argv[2]), "detection_rule_ids": req["detection_rule_ids"], "controls": req["required_controls"], "summary": summary, "timeline": timeline}))' "${req}" "${tokens_json}" "${LEARNER_ID}")"

  http_call POST "${CHALLENGE_URL}/api/stages/${stage}/synthesize" "${LEARNER_AUTH}" "${payload}"
  [[ "${RESP_CODE}" == "200" ]] || fail "${stage} synthesis failed (HTTP ${RESP_CODE}): ${RESP_BODY}"
  flag="$(jget "${RESP_BODY}" 'd["hard_flag"]')"
  expected="$(expected_flag "${stage}")"
  [[ "${flag}" == "${expected}" ]] || fail "${stage}: synthesis flag ${flag} does not match local HMAC ${expected}"

  http_call POST "${GATE_URL}/api/flags/submit" "${LEARNER_AUTH}" "{\"learner_id\":\"${LEARNER_ID}\",\"stage_id\":\"${stage}\",\"flag\":\"${flag}\"}"
  [[ "${RESP_CODE}" == "200" ]] || fail "${stage} flag rejected by gate (HTTP ${RESP_CODE}): ${RESP_BODY}"
  accepted="$(jget "${RESP_BODY}" 'd.get("accepted")')"
  [[ "${accepted}" == "True" ]] || fail "${stage}: gate did not accept the flag"
  next="$(jget "${RESP_BODY}" 'd.get("next_stage_id") or ""')"
  expected_next=""
  if [[ $((stage_index + 1)) -lt "${#STAGES[@]}" ]]; then
    expected_next="${STAGES[$((stage_index + 1))]}"
  fi
  [[ "${next}" == "${expected_next}" ]] || fail "${stage}: expected next stage '${expected_next}', got '${next}'"
  promoted_profile_stage="$(jget "${RESP_BODY}" 'd["bank_profile"].get("stage_id")')"
  [[ "${promoted_profile_stage}" == "${expected_next}" ]] || fail "${stage}: bank profile did not promote to '${expected_next}', got '${promoted_profile_stage}'"
  promoted_level="$(jget "${RESP_BODY}" 'd["bank_profile"].get("level")')"
  expected_level=$((stage_index + 2))
  [[ "${promoted_level}" == "${expected_level}" ]] || fail "${stage}: expected bank security level ${expected_level}, got ${promoted_level}"

  if [[ -n "${expected_next}" ]]; then
    log "PASS ${stage} (${#scenario_ids[@]} scenarios) -> ${expected_next}"
  else
    log "PASS ${stage} (${#scenario_ids[@]} scenarios) -> curriculum complete"
  fi
  stage_index=$((stage_index + 1))
done

# --- 5. final state checks --------------------------------------------------
# Re-submitting an accepted flag is idempotent.
re_flag="$(expected_flag "L00-foundation")"
http_call POST "${GATE_URL}/api/flags/submit" "${LEARNER_AUTH}" "{\"learner_id\":\"${LEARNER_ID}\",\"stage_id\":\"L00-foundation\",\"flag\":\"${re_flag}\"}"
[[ "${RESP_CODE}" == "200" ]] || fail "idempotent re-submission failed (HTTP ${RESP_CODE}): ${RESP_BODY}"
re_status="$(jget "${RESP_BODY}" 'd.get("status", "")')"
[[ "${re_status}" == "completed" ]] || fail "re-submission status was ${re_status}, expected completed"
log "NEG  idempotent re-submission accepted (completed)"

# Gate curriculum must report every stage completed.
http_call GET "${GATE_URL}/api/curriculum?learner_id=${LEARNER_ID}" "${LEARNER_AUTH}"
[[ "${RESP_CODE}" == "200" ]] || fail "curriculum check failed (HTTP ${RESP_CODE}): ${RESP_BODY}"
not_completed="$(jget "${RESP_BODY}" '[s["id"] for s in d["stages"] if s["status"] != "completed"]')"
[[ "${not_completed}" == "[]" ]] || fail "stages not completed after the walk: ${not_completed}"

log "RESULT: full 10-stage flag pipeline verified over live HTTP (gate=${GATE_URL}, challenges=${CHALLENGE_URL})"
log "Learner ${LEARNER_ID} completed all 10 stages; progress left in place for inspection"
log "(re-running this check resets the ${COHORT_ID} cohort first)"
