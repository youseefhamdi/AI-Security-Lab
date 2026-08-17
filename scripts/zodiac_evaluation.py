"""Safe held-out evaluation helpers for Zodiac Bank security controls."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Callable, Iterable


class EvaluationError(ValueError):
    pass


def canonical_marker(value: str) -> str:
    return " ".join(str(value).replace("\u200b", "").replace("%20", " ").replace("+", " ").split()).casefold()


def mutate_marker(value: str) -> list[str]:
    value = str(value)
    return [value.upper(), value.replace(" ", "  "), value.replace(" ", "%20"), value.replace(" ", "+"), "\u200b".join(value)]


def evaluate_control_transfer(
    cases: Iterable[dict[str, Any]],
    detector: Callable[[str], bool],
) -> dict[str, Any]:
    """Measure a detector on held-out harmless marker variants."""
    rows = list(cases)
    if not rows:
        raise EvaluationError("at least one held-out case is required")
    true_positive = false_positive = true_negative = false_negative = 0
    mutations = 0
    for case in rows:
        marker = str(case.get("marker", ""))
        expected = bool(case.get("should_alert"))
        variants = [marker] + mutate_marker(marker)
        for variant in variants:
            actual = bool(detector(variant))
            mutations += int(variant != marker)
            if expected and actual:
                true_positive += 1
            elif expected and not actual:
                false_negative += 1
            elif not expected and actual:
                false_positive += 1
            else:
                true_negative += 1
    total = true_positive + true_negative + false_positive + false_negative
    return {
        "cases": len(rows),
        "variants": total,
        "mutations": mutations,
        "true_positive": true_positive,
        "false_negative": false_negative,
        "false_positive": false_positive,
        "true_negative": true_negative,
        "recall": round(true_positive / max(true_positive + false_negative, 1), 4),
        "precision": round(true_positive / max(true_positive + false_positive, 1), 4),
        "synthetic": True,
        "external_egress": False,
    }


def evaluate_scenario_pack(pack: dict[str, Any], *, expected_scenarios: int = 150, expected_gates: int = 75) -> dict[str, Any]:
    scenarios = list(pack.get("scenarios", []))
    gates = list(pack.get("hard_gates", []))
    if len(scenarios) != expected_scenarios or len(gates) != expected_gates:
        raise EvaluationError("scenario pack size does not match the hard-range contract")
    ids = [str(item.get("id")) for item in scenarios]
    if len(ids) != len(set(ids)) or any(not item.get("detection_rule_ids") or not item.get("required_controls") for item in scenarios):
        raise EvaluationError("scenario IDs, detections, and controls must be complete and unique")
    gate_scenarios = [scenario_id for gate in gates for scenario_id in gate.get("scenario_ids", [])]
    if len(gate_scenarios) != expected_gates * 2 or not set(gate_scenarios).issubset(set(ids)):
        raise EvaluationError("hard-gate scenario references are incomplete")
    return {"scenarios": len(scenarios), "hard_gates": len(gates), "unique_scenarios": len(set(ids)), "gate_references": len(gate_scenarios), "synthetic": True}


def deterministic_fixture_hash(fixtures: Iterable[dict[str, Any]]) -> str:
    safe = [{key: value for key, value in item.items() if key not in {"raw_prompt", "secret", "token"}} for item in fixtures]
    return hashlib.sha256(json.dumps(safe, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def run_held_out_evaluation(pack: dict[str, Any]) -> dict[str, Any]:
    """Run repository-independent quality checks over abstract fixtures."""
    range_result = evaluate_scenario_pack(pack)
    cases = [
        {"marker": "synthetic instruction marker", "should_alert": True},
        {"marker": "normal synthetic support question", "should_alert": False},
        {"marker": "synthetic review marker", "should_alert": True},
    ]
    detector = lambda value: "marker" in canonical_marker(value)
    transfer = evaluate_control_transfer(cases, detector)
    fixtures = [{"id": "held-out-1", "class": "prompt-boundary"}, {"id": "held-out-2", "class": "tool-boundary"}]
    return {"range": range_result, "transfer": transfer, "fixture_hash": deterministic_fixture_hash(fixtures), "held_out": True, "model_calls": 0, "external_egress": False}
