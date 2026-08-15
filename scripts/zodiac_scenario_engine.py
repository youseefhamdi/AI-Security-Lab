"""Dependency-free scenario primitives shared by the challenge service and evaluator.

Schema v2: steps declare *evidence value types* (e.g. ``http-method``) instead of
literal expected values. The service derives the per-run expected values with an
HMAC bound to the flag secret, learner ID, scenario ID, step ID, and the run
nonce. The repository therefore never contains a literal answer, and every
learner/run sees a different value set. Steps after the first additionally
require a chained ``proof`` value produced by the previous accepted step.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Any

SCHEMA_VERSION = 2
SAFE_EVENT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")
MAX_ATTEMPTS_PER_STEP = 20
PROOF_KEY = "proof"

# Bounded value vocabularies. The engine selects a per-run value from one of
# these lists using the HMAC digest; the observation text guides the learner.
VOCABULARY: dict[str, list[str]] = {
    "http-method": ["GET", "HEAD", "POST", "PUT", "OPTIONS", "DELETE"],
    "scope-route": ["/public/scope", "/api/models", "/api/support/chat", "/api/rag/query", "/api/agent/dispatch", "/api/memory/search"],
    "admin-route": ["/api/admin/approval", "/api/admin/cohorts", "/api/admin/cohorts/report", "/api/admin/cohorts/reset"],
    "ci-route": ["/api/ci/artifacts", "/api/logs/search", "/api/campaign/execute", "/api/scenarios", "/api/stages"],
    "header-name": ["X-Zodiac-Bank-Training-Flag", "X-Zodiac-Bank-Scope", "X-Zodiac-Bank-Provider", "X-Graph-Context-Key", "X-Training-Learner-Token", "X-Forwarded-User"],
    "entity-id": ["ZB-CUS-001", "ZB-CUS-002", "ZB-CUS-004", "ZB-CASE-002", "ZB-CASE-006", "ZB-EMP-001"],
    "case-id": ["ZB-CASE-002", "ZB-CASE-003", "ZB-CASE-006", "ZB-CASE-009"],
    "worker-id": ["risk-engine", "support-router", "fraud-analyst", "approval-worker", "credit-officer"],
    "trust-class": ["synthetic-training-only", "retrieved-untrusted-data", "evidence-not-authority", "unverified", "quarantined", "denied"],
    "loopback-addr": ["127.0.0.1", "127.0.0.2", "127.0.0.3", "localhost"],
    "mode": ["normal", "stealth", "verbose", "bounded", "diagnostic"],
    "boolean": ["true", "false"],
    "result": ["permitted", "denied", "suspicious", "blocked", "deferred", "withheld"],
    "decision": ["deny-and-quarantine", "stop-and-reapprove", "deny-and-alert", "hold", "allow-data-only", "stop-and-review"],
    "control": ["approval-required", "output-validation", "tenant-filter", "provenance", "rollback", "manifest-pinning", "audience-binding", "least-privilege", "canonicalization", "circuit-breaker"],
    "detection-rule": ["ZB-AI-001", "ZB-AI-002", "ZB-AI-003", "ZB-AI-004", "ZB-AI-005", "ZB-AI-006", "ZB-AI-007", "ZB-AI-008", "ZB-AI-009", "ZB-AI-010"],
    "tool-name": ["add_numbers", "send_notification", "search_documents", "read_file", "execute_sql", "list_directory"],
    "package-name": ["zodiac-risk-parser", "fastparserx", "dataframe-utils", "auth-helper", "zodiac-notify"],
    "publisher": ["synthetic-registry", "synthetic-mirror", "untrusted-mirror", "approved-registry"],
    "digest-state": ["pinned", "changed", "mismatch", "approved", "unapproved"],
    "occlusion": ["visible", "hidden", "parsed-only", "split", "obfuscated"],
    "side-effect": ["none", "denied", "disabled", "blocked"],
    "marker": ["baseline", "anomaly", "canary", "benign", "declared", "over-limit"],
    "chained-proof": ["chained-proof"],
}



def validate_scenarios(document: dict[str, Any], curriculum: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if document.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"unsupported scenario schema: expected v{SCHEMA_VERSION}")
    if document.get("scope", {}).get("classification") != "synthetic-training-only":
        errors.append("scenario pack must be synthetic-training-only")
    if document.get("scope", {}).get("side_effects") is not False:
        errors.append("scenario pack must forbid side effects")
    curriculum_ids = {str(stage["id"]) for stage in curriculum.get("stages", [])}
    scenarios = document.get("scenarios", [])
    scenario_ids: set[str] = set()
    by_stage: dict[str, list[str]] = {}
    for scenario in scenarios:
        scenario_id = str(scenario.get("id", ""))
        if not scenario_id or scenario_id in scenario_ids:
            errors.append(f"duplicate or missing scenario ID: {scenario_id}")
        scenario_ids.add(scenario_id)
        stage_id = scenario.get("stage_id")
        if stage_id not in curriculum_ids:
            errors.append(f"scenario {scenario_id} references unknown stage")
        by_stage.setdefault(str(stage_id), []).append(scenario_id)
        if scenario.get("difficulty", 0) < 1 or not scenario.get("objective"):
            errors.append(f"scenario {scenario_id} lacks difficulty or objective")
        steps = scenario.get("steps", [])
        if len(steps) < 2 or len(steps) > 8:
            errors.append(f"scenario {scenario_id} must contain 2-8 bounded steps")
        step_ids: set[str] = set()
        for index, step in enumerate(steps):
            step_id = str(step.get("id", ""))
            event = str(step.get("event", ""))
            if not step_id or step_id in step_ids or not SAFE_EVENT_PATTERN.fullmatch(event):
                errors.append(f"scenario {scenario_id} has invalid step/event")
            step_ids.add(step_id)
            evidence = step.get("evidence")
            if not isinstance(evidence, dict) or not evidence:
                errors.append(f"scenario {scenario_id} step {step_id} lacks evidence type mapping")
            else:
                for key, value_type in evidence.items():
                    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", str(key)):
                        errors.append(f"scenario {scenario_id} step {step_id} has an invalid evidence key")
                    if value_type not in VOCABULARY:
                        errors.append(f"scenario {scenario_id} step {step_id} uses unknown evidence type {value_type!r}")
                if index > 0 and PROOF_KEY not in evidence:
                    errors.append(f"scenario {scenario_id} step {step_id} must chain the previous step via '{PROOF_KEY}'")
            if not step.get("observation"):
                errors.append(f"scenario {scenario_id} step {step_id} lacks an observation")
        if len(step_ids) != len(steps):
            errors.append(f"scenario {scenario_id} has duplicate step IDs")
        if not scenario.get("detection_rule_ids") or not scenario.get("required_controls"):
            errors.append(f"scenario {scenario_id} lacks detection rules or controls")
        if scenario.get("branch") not in {"attack", "defense", "forensics", "recovery"}:
            errors.append(f"scenario {scenario_id} has invalid branch")
    requirements = document.get("stage_requirements", {})
    if set(requirements) != curriculum_ids:
        errors.append("every curriculum stage must have an explicit scenario requirement")
    referenced_scenarios: set[str] = set()
    for stage_id, requirement in requirements.items():
        required = requirement.get("scenario_ids", [])
        if not required or len(required) != len(set(required)) or not set(required).issubset(scenario_ids):
            errors.append(f"stage {stage_id} has missing or duplicate required scenarios")
        referenced_scenarios.update(required)
        if stage_id not in curriculum_ids:
            errors.append(f"stage requirement references unknown stage {stage_id}")
        if not requirement.get("detection_rule_ids") or not requirement.get("required_controls") or not requirement.get("concepts"):
            errors.append(f"stage {stage_id} lacks synthesis requirements")
    if referenced_scenarios != scenario_ids:
        errors.append("every scenario must be required by exactly one stage")
    if errors:
        raise ValueError("; ".join(errors))
    return {"scenarios": len(scenarios), "stages_with_requirements": len(requirements), "scenario_ids": sorted(scenario_ids), "by_stage": by_stage}


def scenario_map(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item["id"]): item for item in document.get("scenarios", [])}


def requirement_for(document: dict[str, Any], stage_id: str) -> dict[str, Any]:
    requirement = document.get("stage_requirements", {}).get(stage_id)
    if requirement is None:
        raise KeyError(f"no scenario requirement for {stage_id}")
    return requirement


def step_for(scenario: dict[str, Any], index: int) -> dict[str, Any]:
    steps = scenario.get("steps", [])
    if index < 0 or index >= len(steps):
        raise IndexError("scenario is already complete")
    return steps[index]


def _digest(secret: bytes, *parts: str) -> bytes:
    return hmac.new(secret, ":".join(parts).encode("utf-8"), hashlib.sha256).digest()


def _pick(vocabulary: list[str], digest: bytes) -> str:
    index = int.from_bytes(digest[:4], "big") % len(vocabulary)
    return vocabulary[index]


def expected_for_step(
    secret: bytes,
    learner_id: str,
    scenario_id: str,
    step: dict[str, Any],
    nonce: str,
    step_index: int,
) -> dict[str, Any]:
    """Derive the per-run expected evidence values for a step.

    Step 2+ additionally expects ``proof``: the chained token issued when the
    previous step was accepted. The token is recomputed from the same HMAC so
    it cannot be forged without the secret.
    """
    expected: dict[str, Any] = {}
    step_id = str(step.get("id", ""))
    for key, value_type in step.get("evidence", {}).items():
        if key == PROOF_KEY:
            continue
        vocabulary = VOCABULARY[value_type]
        expected[key] = _pick(vocabulary, _digest(secret, learner_id, scenario_id, step_id, nonce, key))
    if PROOF_KEY in step.get("evidence", {}):
        expected[PROOF_KEY] = step_token(secret, learner_id, scenario_id, nonce, step_index - 1)
    return expected


def step_token(secret: bytes, learner_id: str, scenario_id: str, nonce: str, step_index: int) -> str:
    digest = _digest(secret, learner_id, scenario_id, nonce, "step-token", str(step_index))
    return f"ZB-STEP-{digest[:20].hex().upper()}"


def candidates_for_step(
    secret: bytes,
    learner_id: str,
    scenario_id: str,
    step: dict[str, Any],
    nonce: str,
    step_index: int,
) -> dict[str, dict[str, Any]]:
    """Candidate pools per evidence key: the correct value plus distractors.

    The correct value is always included; distractors are drawn deterministically
    from the same vocabulary so the pool is stable within a run.
    """
    expected = expected_for_step(secret, learner_id, scenario_id, step, nonce, step_index)
    candidates: dict[str, dict[str, Any]] = {}
    step_id = str(step.get("id", ""))
    for key, value_type in step.get("evidence", {}).items():
        if key == PROOF_KEY:
            continue
        vocabulary = VOCABULARY[value_type]
        correct = expected[key]
        pool = [correct]
        offset = int.from_bytes(_digest(secret, learner_id, scenario_id, step_id, nonce, key, "distract"), "big")
        for i in range(1, len(vocabulary)):
            candidate = vocabulary[(offset + i) % len(vocabulary)]
            if candidate not in pool:
                pool.append(candidate)
            if len(pool) >= min(5, len(vocabulary)):
                break
        candidates[key] = {"correct": correct, "candidates": pool}
    if PROOF_KEY in step.get("evidence", {}):
        candidates[PROOF_KEY] = {"correct": expected[PROOF_KEY], "candidates": [expected[PROOF_KEY]]}
    return candidates


def event_matches(step: dict[str, Any], event: str, evidence: dict[str, Any], expected: dict[str, Any]) -> bool:
    if event != step.get("event"):
        return False
    return set(evidence) == set(expected) and all(evidence.get(key) == value for key, value in expected.items())


def evidence_token(secret: bytes, learner_id: str, scenario_id: str, evidence: list[dict[str, Any]]) -> str:
    canonical = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    digest = hmac.new(secret, f"{learner_id}:{scenario_id}:{canonical}".encode(), hashlib.sha256).hexdigest()
    return f"ZB-EVIDENCE-{scenario_id.upper()}-{digest[:24].upper()}"


def contains_concepts(summary: str, concepts: list[str]) -> bool:
    lowered = summary.lower()
    return all(concept.lower() in lowered for concept in concepts)
