#!/usr/bin/env python3
"""End-to-end verification of the full Zodiac Bank 10-stage flag progression.

This test drives the *real* service implementations (training-gate and
training-challenges) without HTTP: FastAPI is replaced by a minimal stub so the
route handlers run directly against real SQLite state and real HMAC secrets.

Journey covered, per stage in strict order:

  1. learner enrolls via cohort-add (private token issued)
  2. every required scenario is started and solved through its bounded steps,
     using the per-run candidate pools exposed by the hint endpoint (the same
     data the browser trainer UI offers); chained step proofs are honored
  3. stage synthesis validates scenario order, evidence tokens, detection
     coverage, required controls, timeline, and security concepts, then issues
     the hard flag
  4. the gate accepts the flag and unlocks exactly the next stage
  5. after L09 the curriculum reports complete

Negative checks verify invalid flags (401), locked-stage flags (403), wrong
evidence (409), wrong chained proof (409), and idempotent re-submission.

Run directly:  python3 scripts/zodiac_bank_progression_test.py
Import for the evaluator:  from zodiac_bank_progression_test import run_progression
"""

from __future__ import annotations

import importlib.util
import json
import os
import secrets
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# FastAPI stub: the runtime dependency is not installed in offline checkouts.
# The route handlers are plain functions after decoration, so a stub App that
# merely registers routes lets us call the handlers directly with real state.
# ---------------------------------------------------------------------------


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


class _Request:  # noqa: D101
    pass


class _Responses(ModuleType):  # noqa: D101
    class JSONResponse:
        def __init__(self, content: Any = None, status_code: int = 200, headers: dict[str, str] | None = None, **_kwargs: Any) -> None:
            self.content = content
            self.status_code = status_code
            self.headers = headers or {}

    class FileResponse:
        def __init__(self, path: str | Path, **_kwargs: Any) -> None:
            self.path = str(path)


class _Route:
    def __init__(self, path: str, methods: list[str]) -> None:
        self.path = path
        self.methods = methods


class _App:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.routes: list[tuple[_Route, Any]] = []

    def _register(self, path: str, methods: list[str]):
        def decorator(fn: Any) -> Any:
            self.routes.append((_Route(path, methods), fn))
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


def _install_fastapi_stub() -> None:
    if "fastapi" in sys.modules:
        return
    fastapi = ModuleType("fastapi")
    fastapi.FastAPI = _App
    fastapi.Header = _Header
    fastapi.HTTPException = _HTTPException
    fastapi.Request = _Request
    fastapi.Depends = _Depends
    responses = _Responses("fastapi.responses")
    sys.modules["fastapi.responses"] = responses
    fastapi.responses = responses
    sys.modules["fastapi"] = fastapi


# ---------------------------------------------------------------------------
# Environment: strict mode, fresh per-run secret, isolated temp state. These
# must be set before the service modules are imported (they capture paths and
# secrets at import time).
# ---------------------------------------------------------------------------

_TMP = Path(tempfile.mkdtemp(prefix="zodiac-bank-progression-"))
_FLAG_SECRET = secrets.token_hex(32)
_ADMIN_KEY = secrets.token_hex(32)

os.environ["TRAINING_FLAG_SECRET"] = _FLAG_SECRET
os.environ["TRAINING_ADMIN_KEY"] = _ADMIN_KEY
os.environ["TRAINING_SECURITY_MODE"] = "strict"
os.environ["TRAINING_CURRICULUM"] = str(ROOT / "training-config" / "curriculum.json")
os.environ["TRAINING_SCENARIOS"] = str(ROOT / "training-config" / "scenarios.json")
os.environ["TRAINING_STATE_DB"] = str(_TMP / "progress.sqlite3")
os.environ["TRAINING_ACCESS_DB"] = str(_TMP / "progress.sqlite3")
os.environ["TRAINING_CHALLENGE_STATE_DB"] = str(_TMP / "challenges.sqlite3")
os.environ["TRAINING_ARTIFACT_DIR"] = str(_TMP / "learners")

_install_fastapi_stub()


def _load_service(name: str, relative_path: str) -> Any:
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"cannot load {relative_path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


GATE = _load_service("zb_gate_service", "training-gate/main.py")
CHALLENGE = _load_service("zb_challenge_service", "training-challenges/main.py")
SCENARIO_PACK = json.loads((ROOT / "training-config" / "scenarios.json").read_text(encoding="utf-8"))
STAGE_IDS = [stage["id"] for stage in json.loads((ROOT / "training-config" / "curriculum.json").read_text(encoding="utf-8"))["stages"]]


def _reset_state() -> None:
    for child in _TMP.iterdir():
        if child.is_file():
            child.unlink()
        elif child.is_dir():
            for nested in child.iterdir():
                if nested.is_file():
                    nested.unlink()


def _expect_http(fn: Any, status_code: int, what: str) -> None:
    try:
        fn()
    except _HTTPException as exc:
        if exc.status_code != status_code:
            raise AssertionError(f"{what}: expected HTTP {status_code}, got {exc.status_code}: {exc.detail}")
        return
    raise AssertionError(f"{what}: expected HTTP {status_code}, no error raised")


def _solve_scenario(learner_id: str, token: str, scenario_id: str) -> str:
    """Solve one scenario via the trainer-facing API and return its evidence token."""
    started = CHALLENGE.start_scenario(scenario_id, {"learner_id": learner_id}, token)
    assert started["scenario"]["status"] == "active", f"{scenario_id} did not start"
    evidence_token: str | None = None
    while True:
        hint = CHALLENGE.scenario_hint(scenario_id, learner_id=learner_id, x_training_learner_token=token)
        if hint.get("status") != "active":
            raise AssertionError(f"{scenario_id}: expected an active step, got {hint.get('status')}")
        evidence = {key: value["correct"] for key, value in hint["candidates"].items()}
        response = CHALLENGE.scenario_event(
            scenario_id,
            {"learner_id": learner_id, "event": hint["event"], "evidence": evidence},
            token,
        )
        assert response["accepted"] is True, f"{scenario_id}: evidence rejected"
        if response["status"] == "complete":
            evidence_token = response["evidence_token"]
            break
    assert evidence_token, f"{scenario_id}: no evidence token issued"
    return evidence_token


def run_progression() -> dict[str, Any]:
    """Walk all 10 stages end to end. Returns a machine-readable report.

    Raises AssertionError on the first failed assertion so the evaluator can
    treat the whole journey as a single regression check.
    """
    _reset_state()
    stages_report: list[dict[str, Any]] = []
    negatives: dict[str, Any] = {}

    learner = "e2e-trainer-01"
    cohort = "e2e-cohort"
    GATE.create_cohort(GATE.CohortRequest(cohort_id=cohort, display_name="End-to-End Verification"), _=None)
    member = GATE.add_cohort_member(cohort, GATE.CohortMemberRequest(learner_id=learner), _=None)
    token = member["learner_token"]
    assert token and member["status"] == "member"

    for position, stage_id in enumerate(STAGE_IDS):
        requirement = SCENARIO_PACK["stage_requirements"][stage_id]
        next_stage = STAGE_IDS[position + 1] if position + 1 < len(STAGE_IDS) else None

        # Both services must agree the learner is currently on this stage.
        gate_view = GATE.curriculum(learner_id=learner, x_training_learner_token=token)
        statuses = {stage["id"]: stage["status"] for stage in gate_view["stages"]}
        assert statuses[stage_id] == "unlocked", f"{stage_id}: gate status is {statuses[stage_id]}"
        assert CHALLENGE.current_stage(learner) == stage_id, f"{stage_id}: challenge service stage mismatch"

        tokens: list[str] = []
        for scenario_id in requirement["scenario_ids"]:
            tokens.append(_solve_scenario(learner, token, scenario_id))

        summary = "Synthetic incident summary: " + ", ".join(requirement["concepts"]) + " observed with approval gates, provenance, and a complete timeline; all activity confined to the localhost scope."
        timeline = [{"event": f"observed-{sid}", "scenario": sid} for sid in requirement["scenario_ids"]]
        synthesis = CHALLENGE.synthesize_stage(
            stage_id,
            {
                "learner_id": learner,
                "scenario_ids": requirement["scenario_ids"],
                "evidence_tokens": tokens,
                "detection_rule_ids": requirement["detection_rule_ids"],
                "controls": requirement["required_controls"],
                "summary": summary,
                "timeline": timeline,
            },
            token,
        )
        hard_flag = synthesis["hard_flag"]
        # The flag issued by synthesis must be byte-identical to both services'
        # HMAC formula over the same secret and stage ID.
        assert hard_flag == CHALLENGE.flag_for(stage_id) == GATE.flag_for(stage_id)
        assert hard_flag.startswith(f"ZODIAC-BANK-{stage_id.upper()}-") and len(hard_flag.split("-")[-1]) == 32

        submission = GATE.submit_flag(
            GATE.FlagSubmission(learner_id=learner, stage_id=stage_id, flag=hard_flag),
            x_training_learner_token=token,
        )
        assert submission["accepted"] is True and submission["status"] == "completed"
        assert submission["next_stage_id"] == next_stage, f"{stage_id}: unexpected next stage {submission['next_stage_id']}"
        # Challenge service observes the shared progress DB advancing too.
        assert CHALLENGE.current_stage(learner) == next_stage, f"{stage_id}: challenge service did not observe unlock"

        stages_report.append(
            {
                "stage_id": stage_id,
                "scenarios": len(requirement["scenario_ids"]),
                "steps_solved": sum(
                    len(CHALLENGE.SCENARIO_BY_ID[sid]["steps"]) for sid in requirement["scenario_ids"]
                ),
                "flag_issued": hard_flag,
                "next_stage_id": next_stage,
            }
        )

    final_view = GATE.curriculum(learner_id=learner, x_training_learner_token=token)
    final_statuses = {stage["id"]: stage["status"] for stage in final_view["stages"]}
    assert all(status == "completed" for status in final_statuses.values()), "not every stage completed"
    assert CHALLENGE.current_stage(learner) is None, "challenge service still reports an active stage"

    # --- Negative checks on a separate learner so the main journey stays clean ---
    neg_learner = "e2e-neg-01"
    member2 = GATE.add_cohort_member(cohort, GATE.CohortMemberRequest(learner_id=neg_learner), _=None)
    neg_token = member2["learner_token"]

    # Scenario-level negatives run while the negative learner is still on L00 so
    # the challenge service accepts its current-stage scenario.
    neg_scenario = SCENARIO_PACK["stage_requirements"]["L00-foundation"]["scenario_ids"][0]
    CHALLENGE.start_scenario(neg_scenario, {"learner_id": neg_learner}, neg_token)
    hint = CHALLENGE.scenario_hint(neg_scenario, learner_id=neg_learner, x_training_learner_token=neg_token)
    wrong_evidence = {key: value["correct"] for key, value in hint["candidates"].items()}
    first_key = next(iter(wrong_evidence))
    wrong_evidence[first_key] = "GET" if wrong_evidence[first_key] != "GET" else "POST"
    _expect_http(
        lambda: CHALLENGE.scenario_event(
            neg_scenario,
            {"learner_id": neg_learner, "event": hint["event"], "evidence": wrong_evidence},
            neg_token,
        ),
        409,
        "wrong evidence",
    )
    negatives["wrong_evidence_rejected"] = True

    # Wrong chained proof: correct values, tampered proof token.
    correct = CHALLENGE.scenario_event(
        neg_scenario,
        {"learner_id": neg_learner, "event": hint["event"], "evidence": {key: value["correct"] for key, value in hint["candidates"].items()}},
        neg_token,
    )
    assert correct["accepted"] is True and correct["status"] == "active"
    hint2 = CHALLENGE.scenario_hint(neg_scenario, learner_id=neg_learner, x_training_learner_token=neg_token)
    evidence2 = {key: value["correct"] for key, value in hint2["candidates"].items()}
    if "proof" in evidence2:
        evidence2["proof"] = "ZB-STEP-00000000000000000000"
        _expect_http(
            lambda: CHALLENGE.scenario_event(
                neg_scenario,
                {"learner_id": neg_learner, "event": hint2["event"], "evidence": evidence2},
                neg_token,
            ),
            409,
            "wrong chained proof",
        )
        negatives["wrong_proof_rejected"] = True
    else:
        negatives["wrong_proof_rejected"] = "skipped (single-step check scenario)"

    # Flag-level negatives: invalid flag, locked-stage flag, then accept + idempotent re-submit.
    _expect_http(
        lambda: GATE.submit_flag(
            GATE.FlagSubmission(learner_id=neg_learner, stage_id="L00-foundation", flag="ZODIAC-BANK-L00-FOUNDATION-WRONG"),
            x_training_learner_token=neg_token,
        ),
        401,
        "invalid flag",
    )
    negatives["invalid_flag_rejected"] = True

    locked_flag = GATE.flag_for("L02-prompt-injection")
    _expect_http(
        lambda: GATE.submit_flag(
            GATE.FlagSubmission(learner_id=neg_learner, stage_id="L02-prompt-injection", flag=locked_flag),
            x_training_learner_token=neg_token,
        ),
        403,
        "locked-stage flag",
    )
    negatives["locked_stage_rejected"] = True

    valid_flag = GATE.flag_for("L00-foundation")
    first = GATE.submit_flag(
        GATE.FlagSubmission(learner_id=neg_learner, stage_id="L00-foundation", flag=valid_flag),
        x_training_learner_token=neg_token,
    )
    assert first["accepted"] is True and first["next_stage_id"] == "L01-recon"
    second = GATE.submit_flag(
        GATE.FlagSubmission(learner_id=neg_learner, stage_id="L00-foundation", flag=valid_flag),
        x_training_learner_token=neg_token,
    )
    assert second["accepted"] is True and second["status"] == "completed"
    negatives["resubmission_idempotent"] = True

    return {
        "passed": True,
        "learner": learner,
        "stages_completed": len(stages_report),
        "total_scenarios": sum(entry["scenarios"] for entry in stages_report),
        "stages": stages_report,
        "negatives": negatives,
    }


def main() -> int:
    report = run_progression()
    print("Zodiac Bank full 10-stage flag progression — END-TO-END")
    print("-" * 78)
    for entry in report["stages"]:
        flag = entry["flag_issued"]
        compact = f"{flag[:44]}...{flag[-12:]}"
        print(f"  PASS {entry['stage_id']:26} {entry['scenarios']} scenarios, {entry['steps_solved']:>3} steps  -> {compact}")
        print(f"       unlocked: {entry['next_stage_id']}")
    print("-" * 78)
    for name, value in report["negatives"].items():
        print(f"  NEG  {name}: {'PASS' if value is True else value}")
    print("-" * 78)
    print(f"  RESULT: all {report['stages_completed']} stages completed, {report['total_scenarios']} scenarios solved, "
          "flag pipeline verified end-to-end")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
