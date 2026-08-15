"""Dependency-free scenario primitives shared by the challenge service and evaluator."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Any

SCHEMA_VERSION = 1
SAFE_EVENT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")


def validate_scenarios(document: dict[str, Any], curriculum: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if document.get("schema_version") != SCHEMA_VERSION:
        errors.append("unsupported scenario schema")
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
        for step in steps:
            step_id = str(step.get("id", ""))
            event = str(step.get("event", ""))
            if not step_id or step_id in step_ids or not SAFE_EVENT_PATTERN.fullmatch(event):
                errors.append(f"scenario {scenario_id} has invalid step/event")
            step_ids.add(step_id)
            if not isinstance(step.get("match"), dict) or not step["match"]:
                errors.append(f"scenario {scenario_id} step {step_id} lacks bounded evidence matcher")
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


def event_matches(step: dict[str, Any], event: str, evidence: dict[str, Any]) -> bool:
    if event != step.get("event"):
        return False
    expected = step.get("match", {})
    return set(evidence) == set(expected) and all(evidence.get(key) == value for key, value in expected.items())


def evidence_token(secret: bytes, learner_id: str, scenario_id: str, evidence: list[dict[str, Any]]) -> str:
    canonical = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    digest = hmac.new(secret, f"{learner_id}:{scenario_id}:{canonical}".encode(), hashlib.sha256).hexdigest()
    return f"ZB-EVIDENCE-{scenario_id.upper()}-{digest[:24].upper()}"


def contains_concepts(summary: str, concepts: list[str]) -> bool:
    lowered = summary.lower()
    return all(concept.lower() in lowered for concept in concepts)
