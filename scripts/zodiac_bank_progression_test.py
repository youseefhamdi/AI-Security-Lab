#!/usr/bin/env python3
"""End-to-end verification of the 100-scenario, 50-hard-gate Zodiac Bank range.

The harness drives the real gate and challenge handlers with FastAPI stubbed only
for offline environments. Each of the 50 gates requires two scenarios, chained
per-run evidence, gate synthesis, a gate HMAC flag, and gate submission. The
fifth gate in each stage completes that stage and promotes the dynamic bank
profile.
"""

from __future__ import annotations

import importlib.util
import json
import os
import secrets
import shutil
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from zodiac_scenario_engine import load_scenario_pack


class _HTTPException(Exception):
    def __init__(self, status_code: int, detail: Any = None, **_kwargs: Any) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class _Header:
    def __init__(self, default: Any = "") -> None:
        self.default = default


class _Depends:
    def __init__(self, dependency: Any = None, **_kwargs: Any) -> None:
        self.dependency = dependency


class _Request:
    pass


class _Responses(ModuleType):
    class JSONResponse:
        def __init__(self, content: Any = None, status_code: int = 200, headers: dict[str, str] | None = None, **_kwargs: Any) -> None:
            self.content = content
            self.status_code = status_code
            self.headers = headers or {}

    class FileResponse:
        def __init__(self, path: str | Path, **_kwargs: Any) -> None:
            self.path = str(path)


class _App:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.routes: list[Any] = []

    def _register(self, path: str, methods: list[str]):
        def decorator(fn: Any) -> Any:
            self.routes.append((path, methods, fn))
            return fn
        return decorator

    def get(self, path: str, **kwargs: Any) -> Any:
        return self._register(path, ["GET"])

    def post(self, path: str, **kwargs: Any) -> Any:
        return self._register(path, ["POST"])

    def api_route(self, path: str, methods: list[str] | None = None, **kwargs: Any) -> Any:
        return self._register(path, methods or ["GET"])

    def middleware(self, kind: str) -> Any:
        return lambda fn: fn


def install_fastapi_stub() -> None:
    fastapi = ModuleType("fastapi")
    fastapi.FastAPI = _App
    fastapi.Header = _Header
    fastapi.HTTPException = _HTTPException
    fastapi.Request = _Request
    fastapi.Depends = _Depends
    responses = _Responses("fastapi.responses")
    fastapi.responses = responses
    sys.modules["fastapi"] = fastapi
    sys.modules["fastapi.responses"] = responses


TMP = Path(tempfile.mkdtemp(prefix="zodiac-bank-gates-"))
FLAG_SECRET = secrets.token_hex(32)
os.environ.update({
    "TRAINING_FLAG_SECRET": FLAG_SECRET,
    "TRAINING_ADMIN_KEY": secrets.token_hex(32),
    "TRAINING_SECURITY_MODE": "strict",
    "TRAINING_CURRICULUM": str(ROOT / "training-config" / "curriculum.json"),
    "TRAINING_SCENARIOS": str(ROOT / "training-config" / "scenarios.json"),
    "TRAINING_STATE_DB": str(TMP / "progress.sqlite3"),
    "TRAINING_ACCESS_DB": str(TMP / "progress.sqlite3"),
    "TRAINING_CHALLENGE_STATE_DB": str(TMP / "challenges.sqlite3"),
    "TRAINING_ARTIFACT_DIR": str(TMP / "learners"),
})
install_fastapi_stub()


def load_service(name: str, relative_path: str) -> Any:
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


GATE = load_service("zb_gate_service", "training-gate/main.py")
CHALLENGE = load_service("zb_challenge_service", "training-challenges/main.py")
PACK = load_scenario_pack(ROOT / "training-config" / "scenarios.json")
CURRICULUM = json.loads((ROOT / "training-config" / "curriculum.json").read_text(encoding="utf-8"))
STAGE_IDS = [stage["id"] for stage in CURRICULUM["stages"]]
GATES_BY_STAGE = {stage_id: [gate for gate in PACK["hard_gates"] if gate["stage_id"] == stage_id] for stage_id in STAGE_IDS}


def reset_state() -> None:
    if TMP.exists():
        for child in TMP.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()


def expect_http(fn: Any, status: int, label: str) -> None:
    try:
        fn()
    except _HTTPException as exc:
        if exc.status_code != status:
            raise AssertionError(f"{label}: expected HTTP {status}, got {exc.status_code}: {exc.detail}")
        return
    raise AssertionError(f"{label}: expected HTTP {status}, no error raised")


def solve_scenario(learner: str, token: str, scenario_id: str) -> str:
    started = CHALLENGE.start_scenario(scenario_id, {"learner_id": learner}, token)
    assert started["scenario"]["status"] == "active"
    while True:
        hint = CHALLENGE.scenario_hint(scenario_id, learner_id=learner, x_training_learner_token=token)
        assert hint["status"] == "active"
        evidence = {key: value["correct"] for key, value in hint["candidates"].items()}
        result = CHALLENGE.scenario_event(scenario_id, {"learner_id": learner, "event": hint["event"], "evidence": evidence}, token)
        assert result["accepted"] is True
        if result["status"] == "complete":
            return str(result["evidence_token"])


def synthesize_gate(learner: str, token: str, gate: dict[str, Any], tokens: list[str]) -> dict[str, Any]:
    summary = "Synthetic gate evidence covers " + ", ".join(gate["concepts"]) + " with bounded authorization, provenance, and localhost-only controls."
    body = {
        "learner_id": learner,
        "scenario_ids": gate["scenario_ids"],
        "evidence_tokens": tokens,
        "detection_rule_ids": gate["detection_rule_ids"],
        "controls": gate["required_controls"],
        "timeline": [{"event": f"observed-{scenario_id}", "scenario": scenario_id} for scenario_id in gate["scenario_ids"]],
        "summary": summary,
    }
    return CHALLENGE.synthesize_gate(gate["gate_id"], body, token)


def enroll(cohort: str, learner: str) -> str:
    try:
        GATE.create_cohort(GATE.CohortRequest(cohort_id=cohort, display_name="100 Scenario Verification"), _=None)
    except _HTTPException as exc:
        if exc.status_code != 409:
            raise
    member = GATE.add_cohort_member(cohort, GATE.CohortMemberRequest(learner_id=learner), _=None)
    return str(member["learner_token"])


def run_progression() -> dict[str, Any]:
    reset_state()
    learner = "gate-e2e-trainer"
    token = enroll("gate-e2e-cohort", learner)
    stages: list[dict[str, Any]] = []
    gate_count = 0
    scenario_count = 0

    for stage_index, stage_id in enumerate(STAGE_IDS):
        stage_gates = GATES_BY_STAGE[stage_id]
        assert len(stage_gates) == 5
        stage_scenarios = 0
        for gate in stage_gates:
            current = CHALLENGE.list_hard_gates(learner_id=learner, x_training_learner_token=token)
            assert current["current_gate_id"] == gate["gate_id"], f"expected {gate['gate_id']}, got {current['current_gate_id']}"
            tokens = [solve_scenario(learner, token, scenario_id) for scenario_id in gate["scenario_ids"]]
            scenario_count += len(tokens)
            stage_scenarios += len(tokens)
            synthesis = synthesize_gate(learner, token, gate, tokens)
            flag = synthesis["hard_flag"]
            assert flag == CHALLENGE.gate_flag_for(gate["gate_id"]) == GATE.gate_flag_for(gate["gate_id"])
            result = GATE.submit_gate(GATE.GateSubmission(learner_id=learner, gate_id=gate["gate_id"], flag=flag), x_training_learner_token=token)
            assert result["accepted"] is True
            gate_count += 1
            assert result["hard_gate_count"] == gate_count
            final_gate = gate is stage_gates[-1]
            assert result["stage_completed"] is final_gate
            if final_gate:
                expected_next = STAGE_IDS[stage_index + 1] if stage_index + 1 < len(STAGE_IDS) else None
                assert result["next_stage_id"] == expected_next
                assert result["bank_profile"]["promotion_count"] == stage_index + 1
        stages.append({"stage_id": stage_id, "gates": len(stage_gates), "scenarios": stage_scenarios, "next_stage_id": STAGE_IDS[stage_index + 1] if stage_index + 1 < len(STAGE_IDS) else None})

    final = GATE.curriculum(learner_id=learner, x_training_learner_token=token)
    assert all(stage["status"] == "completed" for stage in final["stages"])
    assert CHALLENGE.current_stage(learner) is None
    assert GATE.bank_profile(learner_id=learner, x_training_learner_token=token)["profile"]["profile_id"] == "apt-complete-review"

    # Negative paths on a fresh learner.
    negative_learner = "gate-e2e-negative"
    negative_token = enroll("gate-e2e-cohort", negative_learner)
    first_gate = GATES_BY_STAGE[STAGE_IDS[0]][0]
    second_gate = GATES_BY_STAGE[STAGE_IDS[0]][1]
    expect_http(lambda: GATE.submit_gate(GATE.GateSubmission(learner_id=negative_learner, gate_id=second_gate["gate_id"], flag=GATE.gate_flag_for(second_gate["gate_id"])), x_training_learner_token=negative_token), 403, "locked gate")
    locked_synthesis = {
        "learner_id": negative_learner,
        "scenario_ids": second_gate["scenario_ids"],
        "evidence_tokens": ["not-issued-1", "not-issued-2"],
        "detection_rule_ids": second_gate["detection_rule_ids"],
        "controls": second_gate["required_controls"],
        "timeline": [{"event": "unreachable-1"}, {"event": "unreachable-2"}],
        "summary": "Synthetic locked gate evidence covers " + ", ".join(second_gate["concepts"]),
    }
    expect_http(lambda: CHALLENGE.synthesize_gate(second_gate["gate_id"], locked_synthesis, negative_token), 403, "locked gate synthesis")
    expect_http(lambda: CHALLENGE.synthesize_stage(STAGE_IDS[0], {"learner_id": negative_learner}, negative_token), 410, "retired stage synthesis")
    expect_http(lambda: GATE.submit_gate(GATE.GateSubmission(learner_id=negative_learner, gate_id=first_gate["gate_id"], flag="invalid"), x_training_learner_token=negative_token), 401, "invalid gate flag")
    scenario_id = first_gate["scenario_ids"][0]
    CHALLENGE.start_scenario(scenario_id, {"learner_id": negative_learner}, negative_token)
    hint = CHALLENGE.scenario_hint(scenario_id, learner_id=negative_learner, x_training_learner_token=negative_token)
    wrong = {key: value["correct"] for key, value in hint["candidates"].items()}
    first_key = next(iter(wrong))
    wrong[first_key] = "GET" if wrong[first_key] != "GET" else "POST"
    expect_http(lambda: CHALLENGE.scenario_event(scenario_id, {"learner_id": negative_learner, "event": hint["event"], "evidence": wrong}, negative_token), 409, "wrong evidence")
    negatives = {"locked_gate_rejected": True, "locked_gate_synthesis_rejected": True, "retired_stage_synthesis_rejected": True, "invalid_gate_rejected": True, "wrong_evidence_rejected": True}

    # Complete the first gate and ensure re-submission is idempotent.
    tokens = [solve_scenario(negative_learner, negative_token, sid) for sid in first_gate["scenario_ids"]]
    synthesis = synthesize_gate(negative_learner, negative_token, first_gate, tokens)
    first_result = GATE.submit_gate(GATE.GateSubmission(learner_id=negative_learner, gate_id=first_gate["gate_id"], flag=synthesis["hard_flag"]), x_training_learner_token=negative_token)
    second_result = GATE.submit_gate(GATE.GateSubmission(learner_id=negative_learner, gate_id=first_gate["gate_id"], flag=synthesis["hard_flag"]), x_training_learner_token=negative_token)
    assert first_result["accepted"] and second_result["status"] == "completed"
    negatives["gate_resubmission_idempotent"] = True

    return {"passed": True, "stages_completed": len(stages), "total_scenarios": scenario_count, "hard_gates_completed": gate_count, "stages": stages, "negatives": negatives}


def main() -> int:
    report = run_progression()
    print("Zodiac Bank 100-scenario / 50-hard-gate progression — END-TO-END")
    print("-" * 82)
    for stage in report["stages"]:
        print(f"  PASS {stage['stage_id']:26} {stage['gates']} hard gates, {stage['scenarios']} scenarios -> {stage['next_stage_id']}")
    for name in report["negatives"]:
        print(f"  NEG  {name}: PASS")
    print("-" * 82)
    print(f"  RESULT: {report['stages_completed']} stages, {report['hard_gates_completed']} hard gates, {report['total_scenarios']} scenarios verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
