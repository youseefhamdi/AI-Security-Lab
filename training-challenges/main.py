"""Zodiac Bank hard-range challenge service.

The service contains synthetic, localhost-only challenges. In strict mode, stages
L00-L09 require multi-step scenario completion and a synthesis submission with
independent evidence, detection rules, controls, and an incident explanation.
No endpoint executes commands, contacts external systems, or performs banking
side effects.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
if SCRIPT_DIR.is_dir() and str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from zodiac_bank_orchestrator import BankOrchestrator  # noqa: E402
from zodiac_bank_profiles import load_profiles, profile_for_stage, public_profile  # noqa: E402
from zodiac_bank_simulator import BankAuthorizationError, BankValidationError  # noqa: E402
from zodiac_scenario_engine import (  # noqa: E402
    MAX_ATTEMPTS_PER_STEP,
    PROOF_KEY,
    candidates_for_step,
    contains_concepts,
    event_matches,
    evidence_token,
    expected_for_step,
    requirement_for,
    scenario_map,
    step_for,
    step_token,
    load_scenario_pack,
    validate_scenarios,
)

DEFAULT_FLAG_SECRET = "zodiac-bank-change-this-training-secret"
FLAG_SECRET_VALUE = os.environ.get("TRAINING_FLAG_SECRET", DEFAULT_FLAG_SECRET)
FLAG_SECRET = FLAG_SECRET_VALUE.encode("utf-8")
SECURITY_MODE = os.environ.get("TRAINING_SECURITY_MODE", "development")
FLAG_HEX_LENGTH = 32
STAGES = [
    "L00-foundation",
    "L01-recon",
    "L02-prompt-injection",
    "L03-rag",
    "L04-agent-protocols",
    "L05-memory",
    "L06-identity-control-plane",
    "L07-supply-chain",
    "L08-detection-evasion",
    "L09-apt-capstone",
]


def find_config(name: str, local_relative: str) -> Path:
    configured = os.environ.get(name)
    candidates = [Path(configured)] if configured else []
    candidates.extend([Path("/app/config") / Path(local_relative).name, Path(__file__).resolve().parent.parent / local_relative])
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError(f"configuration file not found: {local_relative}")


CURRICULUM_PATH = find_config("TRAINING_CURRICULUM", "training-config/curriculum.json")
SCENARIO_PATH = find_config("TRAINING_SCENARIOS", "training-config/scenarios.json")
PROFILE_PATH = find_config("TRAINING_BANK_PROFILES", "training-config/bank-profiles.json")
ACCESS_DB = Path(os.environ.get("TRAINING_ACCESS_DB", "/var/lib/training/progress.sqlite3"))
CHALLENGE_DB = Path(os.environ.get("TRAINING_CHALLENGE_STATE_DB", "/var/lib/training/challenges.sqlite3"))

if SECURITY_MODE == "strict" and (FLAG_SECRET_VALUE == DEFAULT_FLAG_SECRET or len(FLAG_SECRET) < 32):
    raise RuntimeError("strict security requires TRAINING_FLAG_SECRET with at least 32 bytes")

CURRICULUM = json.loads(CURRICULUM_PATH.read_text(encoding="utf-8"))
BANK_PROFILES = load_profiles(PROFILE_PATH)
SCENARIOS = load_scenario_pack(SCENARIO_PATH)
validate_scenarios(SCENARIOS, CURRICULUM)
MAX_ACTIVE_SCENARIOS = int(SCENARIOS.get("scope", {}).get("max_active_scenarios_per_learner", 2))
MAX_BANK_LEARNERS = 64
if MAX_ACTIVE_SCENARIOS < 1 or MAX_ACTIVE_SCENARIOS > 8:
    raise RuntimeError("max_active_scenarios_per_learner must be between 1 and 8")
SCENARIO_BY_ID = scenario_map(SCENARIOS)
GATES = list(SCENARIOS.get("hard_gates", []))
GATES_BY_ID = {str(gate["gate_id"]): gate for gate in GATES}
GATES_BY_STAGE = {stage_id: [gate for gate in GATES if gate["stage_id"] == stage_id] for stage_id in STAGES}
BANK_ORCHESTRATORS: dict[str, BankOrchestrator] = {}
BANK_ORCHESTRATORS_LOCK = threading.Lock()


def bank_orchestrator(learner_id: str) -> BankOrchestrator:
    # FastAPI may serve two first requests for one learner concurrently. Create
    # exactly one isolated memory per learner or a race could replace the first
    # orchestrator and silently lose pending operations.
    with BANK_ORCHESTRATORS_LOCK:
        orchestrator = BANK_ORCHESTRATORS.get(learner_id)
        if orchestrator is None:
            if len(BANK_ORCHESTRATORS) >= MAX_BANK_LEARNERS:
                raise HTTPException(status_code=429, detail="bounded learner bank-memory capacity reached")
            orchestrator = BankOrchestrator()
            BANK_ORCHESTRATORS[learner_id] = orchestrator
        return orchestrator


app = FastAPI(title="Zodiac Bank Hard Challenge Range", version="2.1")

TRAINER_UI = Path(__file__).resolve().parent / "index.html"


@app.middleware("http")
async def security_headers(request: Any, call_next: Any) -> Any:
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def flag_for(stage_id: str) -> str:
    digest = hmac.new(FLAG_SECRET, stage_id.encode("utf-8"), hashlib.sha256).hexdigest()[:FLAG_HEX_LENGTH].upper()
    safe_stage = re.sub(r"[^A-Za-z0-9]+", "-", stage_id).strip("-").upper()
    return f"ZODIAC-BANK-{safe_stage}-{digest}"


def safe_learner(value: Any) -> str:
    learner_id = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", learner_id):
        raise HTTPException(status_code=422, detail="valid learner_id required")
    return learner_id


def safe_stage(stage_id: str) -> str:
    if stage_id not in STAGES:
        raise HTTPException(status_code=404, detail="unknown stage")
    return stage_id


def gate_flag_for(gate_id: str) -> str:
    digest = hmac.new(FLAG_SECRET, f"hard-gate:{gate_id}".encode("utf-8"), hashlib.sha256).hexdigest()[:FLAG_HEX_LENGTH].upper()
    safe_gate = re.sub(r"[^A-Za-z0-9]+", "-", gate_id).strip("-").upper()
    return f"ZODIAC-BANK-GATE-{safe_gate}-{digest}"


def digest_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def validate_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    if not evidence or len(evidence) > 20:
        raise HTTPException(status_code=422, detail="non-empty bounded evidence object required")
    if any(not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", str(key)) for key in evidence):
        raise HTTPException(status_code=422, detail="evidence keys must be bounded identifiers")
    try:
        encoded = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="evidence must be JSON-compatible") from exc
    if len(encoded.encode("utf-8")) > 4096:
        raise HTTPException(status_code=413, detail="evidence payload exceeds 4 KiB")
    for value in evidence.values():
        if isinstance(value, str) and len(value) > 256:
            raise HTTPException(status_code=413, detail="evidence values exceed 256 characters")
        if isinstance(value, (dict, list)):
            raise HTTPException(status_code=422, detail="nested evidence is not accepted")
    return {str(key): value for key, value in evidence.items()}


def require_learner_access(learner_id: str, token: str) -> None:
    if SECURITY_MODE != "strict":
        return
    if not token:
        raise HTTPException(status_code=401, detail="X-Training-Learner-Token required")
    if not ACCESS_DB.is_file():
        raise HTTPException(status_code=503, detail="learner enrollment state unavailable")
    db = sqlite3.connect(ACCESS_DB)
    try:
        row = db.execute("SELECT token_digest FROM learner_access WHERE learner_id=?", (learner_id,)).fetchone()
    finally:
        db.close()
    if row is None or not hmac.compare_digest(str(row[0]), digest_token(token)):
        raise HTTPException(status_code=403, detail="invalid learner token")


def training_db() -> sqlite3.Connection | None:
    if not ACCESS_DB.is_file():
        return None
    db = sqlite3.connect(ACCESS_DB)
    db.row_factory = sqlite3.Row
    return db


def completed_stages(learner_id: str) -> set[str]:
    db = training_db()
    if db is None:
        return set()
    try:
        rows = db.execute("SELECT stage_id FROM completions WHERE learner_id=?", (learner_id,)).fetchall()
        return {str(row["stage_id"]) for row in rows}
    finally:
        db.close()


def current_stage(learner_id: str) -> str | None:
    completed = completed_stages(learner_id)
    return next((stage_id for stage_id in STAGES if stage_id not in completed), None)


def completed_gates(learner_id: str) -> set[str]:
    db = training_db()
    if db is None:
        return set()
    try:
        rows = db.execute("SELECT gate_id FROM gate_completions WHERE learner_id=?", (learner_id,)).fetchall()
        return {str(row["gate_id"]) for row in rows}
    finally:
        db.close()


def current_gate(learner_id: str) -> dict[str, Any] | None:
    stage_id = current_stage(learner_id)
    if stage_id is None:
        return None
    completed = completed_gates(learner_id)
    return next((gate for gate in GATES_BY_STAGE.get(stage_id, []) if gate["gate_id"] not in completed), None)


def bank_profile(learner_id: str) -> dict[str, Any]:
    """Read the gate-promoted profile from the shared progress database."""
    db = training_db()
    if db is not None:
        try:
            row = db.execute(
                "SELECT profile_id, promotion_count, updated_at FROM learner_profiles WHERE learner_id=?",
                (learner_id,),
            ).fetchone()
            if row is not None:
                profile = next((item for item in BANK_PROFILES["profiles"] if item["profile_id"] == row["profile_id"]), None)
                if profile is not None:
                    return public_profile(profile, promotion_count=int(row["promotion_count"]), updated_at=str(row["updated_at"]))
        finally:
            db.close()
    return public_profile(profile_for_stage(BANK_PROFILES, current_stage(learner_id)))


def require_current_stage(learner_id: str, stage_id: str) -> None:
    if SECURITY_MODE != "strict":
        return
    active = current_stage(learner_id)
    if active != stage_id:
        raise HTTPException(status_code=403, detail="scenario is not in the learner's current unlocked stage")


def challenge_db() -> sqlite3.Connection:
    CHALLENGE_DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(CHALLENGE_DB)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout = 5000")
    db.execute("PRAGMA journal_mode = WAL")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS scenario_runs (
            learner_id TEXT NOT NULL,
            scenario_id TEXT NOT NULL,
            stage_id TEXT NOT NULL,
            step_index INTEGER NOT NULL DEFAULT 0,
            evidence_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'active',
            completion_token TEXT,
            nonce TEXT NOT NULL DEFAULT '',
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(learner_id, scenario_id)
        );
        CREATE TABLE IF NOT EXISTS scenario_events (
            learner_id TEXT NOT NULL,
            scenario_id TEXT NOT NULL,
            step_index INTEGER NOT NULL,
            event TEXT NOT NULL,
            evidence_digest TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            PRIMARY KEY(learner_id, scenario_id, step_index)
        );
        CREATE TABLE IF NOT EXISTS gate_completions (
            learner_id TEXT NOT NULL,
            gate_id TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            PRIMARY KEY(learner_id, gate_id)
        );
        """
    )
    # Migrate older runs to the v2 per-run nonce and attempts counter.
    columns = {row[1] for row in db.execute("PRAGMA table_info(scenario_runs)").fetchall()}
    if "nonce" not in columns:
        db.execute("ALTER TABLE scenario_runs ADD COLUMN nonce TEXT NOT NULL DEFAULT ''")
    if "attempts" not in columns:
        db.execute("ALTER TABLE scenario_runs ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
    db.commit()
    return db


def scenario_view(scenario: dict[str, Any], run: sqlite3.Row | None = None) -> dict[str, Any]:
    return {
        "scenario_id": scenario["id"],
        "stage_id": scenario["stage_id"],
        "gate_id": scenario.get("gate_id"),
        "difficulty": scenario["difficulty"],
        "branch": scenario["branch"],
        "title": scenario["title"],
        "objective": scenario["objective"],
        "clues": scenario["clues"],
        "step_count": len(scenario["steps"]),
        "detection_rule_ids": scenario["detection_rule_ids"],
        "required_controls": scenario["required_controls"],
        "status": run["status"] if run else "not-started",
        "progress": f"{run['step_index']}/{len(scenario['steps'])}" if run else f"0/{len(scenario['steps'])}",
        "attempts": int(run["attempts"]) if run else 0,
        "max_attempts": MAX_ATTEMPTS_PER_STEP,
    }


def solved(stage_id: str, explanation: str, *, synthesis: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "stage_id": stage_id,
        "finding": explanation,
        "next_action": "Submit hard_flag to the Zodiac Bank Training Gate." if synthesis or SECURITY_MODE != "strict" else "Complete the required multi-step scenarios and hard-gate synthesis before submitting a flag.",
    }
    if SECURITY_MODE == "strict" and not synthesis:
        result.update({"hard_range": True, "message": "Complete the required multi-step scenarios and hard-gate synthesis before a hard flag is issued."})
    else:
        result["hard_flag"] = flag_for(stage_id)
    return result


@app.get("/")
def trainer_index() -> FileResponse:
    if not TRAINER_UI.is_file():
        raise HTTPException(status_code=404, detail="trainer UI not packaged")
    return FileResponse(TRAINER_UI)


@app.get("/api/range")
def trainer_range(x_training_learner_token: str = Header(default=""), learner_id: str = "") -> dict[str, Any]:
    """Read-only range map for the trainer UI; never returns step matchers or flags."""
    learner_id = safe_learner(learner_id)
    require_learner_access(learner_id, x_training_learner_token)
    active = current_stage(learner_id)
    return {
        "learner_id": learner_id,
        "current_stage_id": active,
        "bank_profile": bank_profile(learner_id),
        "stages": [
            {
                "stage_id": stage_id,
                "hard_gate_ids": [gate["gate_id"] for gate in GATES_BY_STAGE.get(stage_id, [])],
                "hard_gate_count": len(GATES_BY_STAGE.get(stage_id, [])),
                "scenarios": [
                    {
                        "scenario_id": item["id"],
                        "difficulty": item["difficulty"],
                        "branch": item["branch"],
                        "title": item["title"],
                        "objective": item["objective"],
                        "clues": item["clues"],
                        "step_count": len(item["steps"]),
                        "detection_rule_ids": item["detection_rule_ids"],
                        "required_controls": item["required_controls"],
                    }
                    for item in SCENARIO_BY_ID.values()
                    if item["stage_id"] == stage_id
                ],
            }
            for stage_id in STAGES
        ],
    }


@app.get("/api/scenarios/{scenario_id}/hint")
def scenario_hint(scenario_id: str, learner_id: str = "", x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    """Return the next step's event name, observation, and required evidence keys.

    Intentionally returns keys without their expected values so the learner must
    still derive the evidence from the local training surface.
    """
    learner_id = safe_learner(learner_id)
    require_learner_access(learner_id, x_training_learner_token)
    scenario = SCENARIO_BY_ID.get(scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="unknown scenario")
    require_current_stage(learner_id, scenario["stage_id"])
    db = challenge_db()
    try:
        run = db.execute("SELECT * FROM scenario_runs WHERE learner_id=? AND scenario_id=?", (learner_id, scenario_id)).fetchone()
        if run is None or run["status"] == "complete":
            return {"scenario_id": scenario_id, "status": run["status"] if run else "not-started", "progress": f"{run['step_index'] if run else 0}/{len(scenario['steps'])}"}
        step_index = int(run["step_index"])
        step = step_for(scenario, step_index)
        nonce = str(run["nonce"])
        candidates = candidates_for_step(FLAG_SECRET, learner_id, scenario_id, step, nonce, step_index)
        return {
            "scenario_id": scenario_id,
            "status": "active",
            "progress": f"{step_index + 1}/{len(scenario['steps'])}",
            "event": step.get("event"),
            "observation": step.get("observation"),
            "required_evidence_keys": sorted(step.get("evidence", {}).keys()),
            "candidates": candidates,
            "chain_required": PROOF_KEY in step.get("evidence", {}),
        }
    finally:
        db.close()


@app.get("/api/bank/state")
def bank_state(learner_id: str = "", x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    """Expose the current synthetic bank posture, never flags or raw records."""
    learner_id = safe_learner(learner_id)
    require_learner_access(learner_id, x_training_learner_token)
    profile = bank_profile(learner_id)
    return {
        "learner_id": learner_id,
        "stage_id": profile.get("stage_id"),
        "profile": profile,
        "dynamic_rule": "accepted stage flags promote this profile; only the current stage surface is active",
    }


@app.get("/api/gates")
def list_hard_gates(learner_id: str = "", x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    """Return the active stage's five hard-gate milestones without flags."""
    learner_id = safe_learner(learner_id)
    require_learner_access(learner_id, x_training_learner_token)
    stage_id = current_stage(learner_id)
    completed = completed_gates(learner_id)
    visible = []
    for gate in GATES_BY_STAGE.get(stage_id, []):
        visible.append({
            "gate_id": gate["gate_id"],
            "stage_id": gate["stage_id"],
            "rank": gate["rank"],
            "title": gate["title"],
            "scenario_ids": gate["scenario_ids"],
            "detection_rule_ids": gate["detection_rule_ids"],
            "required_controls": gate["required_controls"],
            "concepts": gate["concepts"],
            "status": "completed" if gate["gate_id"] in completed else ("unlocked" if current_gate(learner_id) and current_gate(learner_id)["gate_id"] == gate["gate_id"] else "locked"),
            "flag_format": f"ZODIAC-BANK-GATE-{gate['gate_id'].upper()}-<{FLAG_HEX_LENGTH} HEX CHARACTERS>",
        })
    active = current_gate(learner_id)
    return {
        "learner_id": learner_id,
        "stage_id": stage_id,
        "current_gate_id": active["gate_id"] if active else None,
        "completed_gate_count": len(completed),
        "total_hard_gates": len(GATES),
        "gates": visible,
    }


@app.post("/api/gates/{gate_id}/synthesize")
def synthesize_gate(gate_id: str, body: dict[str, Any], x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id = safe_learner(body.get("learner_id"))
    require_learner_access(learner_id, x_training_learner_token)
    gate = GATES_BY_ID.get(gate_id)
    if gate is None:
        raise HTTPException(status_code=404, detail="unknown hard gate")
    require_current_stage(learner_id, gate["stage_id"])
    active_gate = current_gate(learner_id)
    if active_gate is None or active_gate["gate_id"] != gate_id:
        raise HTTPException(status_code=403, detail="complete the previous hard gate first")
    supplied_scenarios = body.get("scenario_ids")
    supplied_tokens = body.get("evidence_tokens")
    detections = body.get("detection_rule_ids")
    controls = body.get("controls")
    timeline = body.get("timeline")
    summary = str(body.get("summary", "")).strip()
    if not all(isinstance(value, list) for value in (supplied_scenarios, supplied_tokens, detections, controls, timeline)):
        raise HTTPException(status_code=422, detail="hard-gate synthesis fields must be lists")
    if supplied_scenarios != gate["scenario_ids"] or len(supplied_scenarios) != 2:
        raise HTTPException(status_code=409, detail="hard gate requires its two declared scenarios in manifest order")
    if len(detections) != len(set(detections)) or set(detections) != set(gate["detection_rule_ids"]):
        raise HTTPException(status_code=409, detail="hard-gate detection coverage is incomplete")
    if not set(gate["required_controls"]).issubset(set(controls)):
        raise HTTPException(status_code=409, detail="hard-gate control coverage is incomplete")
    if len(timeline) < 2 or not contains_concepts(summary, gate["concepts"]):
        raise HTTPException(status_code=409, detail="hard-gate synthesis lacks timeline or required concepts")
    db = challenge_db()
    try:
        rows = {str(row["scenario_id"]): row for row in db.execute("SELECT * FROM scenario_runs WHERE learner_id=? AND stage_id=?", (learner_id, gate["stage_id"])).fetchall()}
        if any(scenario_id not in rows or rows[scenario_id]["status"] != "complete" for scenario_id in gate["scenario_ids"]):
            raise HTTPException(status_code=409, detail="complete both hard-gate scenarios before synthesis")
        expected_tokens = [str(rows[scenario_id]["completion_token"]) for scenario_id in gate["scenario_ids"]]
        if supplied_tokens != expected_tokens:
            raise HTTPException(status_code=409, detail="hard-gate evidence tokens are invalid or out of order")
        return {
            "gate_id": gate_id,
            "stage_id": gate["stage_id"],
            "hard_flag": gate_flag_for(gate_id),
            "hard_gate": True,
            "synthesis": {"scenario_ids": gate["scenario_ids"], "detections": gate["detection_rule_ids"], "controls": gate["required_controls"], "timeline_events": len(timeline)},
        }
    finally:
        db.close()


def require_financial_operations(learner_id: str) -> dict[str, Any]:
    profile = bank_profile(learner_id)
    if int(profile.get("level", 0)) < 3:
        raise HTTPException(status_code=403, detail="synthetic financial operations unlock after the protected-assistant level")
    return profile


@app.get("/api/bank/snapshot")
def bank_snapshot(learner_id: str = "", x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id = safe_learner(learner_id)
    require_learner_access(learner_id, x_training_learner_token)
    profile = require_financial_operations(learner_id)
    snapshot = bank_orchestrator(learner_id).memory.snapshot(public=True)
    return {"learner_id": learner_id, "profile": profile, "snapshot": snapshot, "side_effects": []}


@app.post("/api/bank/operations/plan")
def plan_bank_operation(body: dict[str, Any], x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id = safe_learner(body.get("learner_id"))
    require_learner_access(learner_id, x_training_learner_token)
    profile = require_financial_operations(learner_id)
    try:
        run = bank_orchestrator(learner_id).plan(
            str(body.get("operation_type", "")),
            str(body.get("actor_worker_id", "")),
            int(body.get("amount_cents", 0)),
            source_account_id=body.get("source_account_id"),
            destination_account_id=body.get("destination_account_id"),
            operation_id=body.get("operation_id"),
            owner_learner_id=learner_id,
        )
    except (BankValidationError, BankAuthorizationError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"learner_id": learner_id, "profile": profile, "loop": run, "side_effects": []}


@app.post("/api/bank/operations/{run_id}/approve")
def approve_bank_operation(run_id: str, body: dict[str, Any], x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id = safe_learner(body.get("learner_id"))
    require_learner_access(learner_id, x_training_learner_token)
    profile = require_financial_operations(learner_id)
    try:
        run = bank_orchestrator(learner_id).approve(run_id, str(body.get("approver_worker_id", "")), owner_learner_id=learner_id)
    except (BankValidationError, BankAuthorizationError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"learner_id": learner_id, "profile": profile, "loop": run, "side_effects": []}


@app.get("/api/bank/memory")
def bank_memory(query: str = "", worker_id: str = "", learner_id: str = "", entity_id: str = "", x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id = safe_learner(learner_id)
    require_learner_access(learner_id, x_training_learner_token)
    profile = require_financial_operations(learner_id)
    if not query.strip() or not worker_id.strip():
        raise HTTPException(status_code=422, detail="query and worker_id are required")
    try:
        packet = bank_orchestrator(learner_id).memory.retrieve_memory(query, worker_id, entity_id or None)
    except (BankValidationError, BankAuthorizationError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"learner_id": learner_id, "profile": profile, "context": packet, "side_effects": []}


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "healthy",
        "service": "zodiac-bank-hard-challenge-range",
        "stages": len(STAGES),
        "scenarios": len(SCENARIO_BY_ID),
        "hard_gates": len(GATES),
        "mode": "strict-scenario-synthesis" if SECURITY_MODE == "strict" else "development-legacy-compatible",
        "scope": "localhost-only",
    }


@app.get("/api/scenarios")
def list_scenarios(stage_id: str = "", x_training_learner_token: str = Header(default=""), learner_id: str = "") -> dict[str, Any]:
    learner_id = safe_learner(learner_id)
    require_learner_access(learner_id, x_training_learner_token)
    active = current_stage(learner_id)
    selected_stage = safe_stage(stage_id) if stage_id else active
    if selected_stage is None:
        return {"learner_id": learner_id, "status": "complete", "scenarios": []}
    require_current_stage(learner_id, selected_stage)
    db = challenge_db()
    try:
        rows = db.execute("SELECT * FROM scenario_runs WHERE learner_id=? AND stage_id=?", (learner_id, selected_stage)).fetchall()
        runs = {str(row["scenario_id"]): row for row in rows}
        scenarios = [scenario_view(item, runs.get(item["id"])) for item in SCENARIO_BY_ID.values() if item["stage_id"] == selected_stage]
        return {"learner_id": learner_id, "stage_id": selected_stage, "required": requirement_for(SCENARIOS, selected_stage), "scenarios": scenarios}
    finally:
        db.close()


@app.get("/api/scenarios/{scenario_id}")
def get_scenario(scenario_id: str, learner_id: str = "", x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id = safe_learner(learner_id)
    require_learner_access(learner_id, x_training_learner_token)
    scenario = SCENARIO_BY_ID.get(scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="unknown scenario")
    require_current_stage(learner_id, scenario["stage_id"])
    db = challenge_db()
    try:
        run = db.execute("SELECT * FROM scenario_runs WHERE learner_id=? AND scenario_id=?", (learner_id, scenario_id)).fetchone()
        return {"learner_id": learner_id, "scenario": scenario_view(scenario, run)}
    finally:
        db.close()


@app.post("/api/scenarios/{scenario_id}/start")
def start_scenario(scenario_id: str, body: dict[str, Any], x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id = safe_learner(body.get("learner_id"))
    require_learner_access(learner_id, x_training_learner_token)
    scenario = SCENARIO_BY_ID.get(scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="unknown scenario")
    require_current_stage(learner_id, scenario["stage_id"])
    db = challenge_db()
    try:
        profile = bank_profile(learner_id)
        active_limit = int(profile.get("agent_policy", {}).get("max_parallel_scenarios", MAX_ACTIVE_SCENARIOS))
        active_limit = max(1, min(MAX_ACTIVE_SCENARIOS, active_limit))
        active_runs = db.execute(
            "SELECT COUNT(*) AS count FROM scenario_runs WHERE learner_id=? AND status='active'",
            (learner_id,),
        ).fetchone()["count"]
        existing = db.execute(
            "SELECT status FROM scenario_runs WHERE learner_id=? AND scenario_id=?",
            (learner_id, scenario_id),
        ).fetchone()
        if existing is None and active_runs >= active_limit:
            raise HTTPException(status_code=409, detail="current bank profile active-scenario budget reached; complete or reset an active scenario")
        now = utc_now()
        nonce = secrets.token_hex(16)
        db.execute(
            "INSERT OR IGNORE INTO scenario_runs(learner_id, scenario_id, stage_id, nonce, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (learner_id, scenario_id, scenario["stage_id"], nonce, now, now),
        )
        db.commit()
        run = db.execute("SELECT * FROM scenario_runs WHERE learner_id=? AND scenario_id=?", (learner_id, scenario_id)).fetchone()
        if str(run["nonce"]) == "":
            db.execute(
                "UPDATE scenario_runs SET nonce=?, updated_at=? WHERE learner_id=? AND scenario_id=?",
                (nonce, now, learner_id, scenario_id),
            )
            db.commit()
        run = db.execute("SELECT * FROM scenario_runs WHERE learner_id=? AND scenario_id=?", (learner_id, scenario_id)).fetchone()
        return {"learner_id": learner_id, "scenario": scenario_view(scenario, run), "message": "Scenario started; a per-run evidence set was issued for this learner."}
    finally:
        db.close()


@app.post("/api/scenarios/{scenario_id}/reset")
def reset_scenario(scenario_id: str, body: dict[str, Any], x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id = safe_learner(body.get("learner_id"))
    require_learner_access(learner_id, x_training_learner_token)
    scenario = SCENARIO_BY_ID.get(scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="unknown scenario")
    require_current_stage(learner_id, scenario["stage_id"])
    db = challenge_db()
    try:
        deleted = db.execute("DELETE FROM scenario_runs WHERE learner_id=? AND scenario_id=?", (learner_id, scenario_id)).rowcount
        db.execute("DELETE FROM scenario_events WHERE learner_id=? AND scenario_id=?", (learner_id, scenario_id))
        db.commit()
        return {"reset": True, "scenario_id": scenario_id, "learner_id": learner_id, "rows_deleted": deleted, "message": "Scenario run reset; start again for a fresh per-run evidence set."}
    finally:
        db.close()


@app.post("/api/scenarios/{scenario_id}/event")
def scenario_event(scenario_id: str, body: dict[str, Any], x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id = safe_learner(body.get("learner_id"))
    require_learner_access(learner_id, x_training_learner_token)
    scenario = SCENARIO_BY_ID.get(scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="unknown scenario")
    require_current_stage(learner_id, scenario["stage_id"])
    event = str(body.get("event", ""))
    evidence = body.get("evidence")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,63}", event) or not isinstance(evidence, dict):
        raise HTTPException(status_code=422, detail="bounded event and evidence object required")
    evidence = validate_evidence(evidence)
    db = challenge_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        run = db.execute("SELECT * FROM scenario_runs WHERE learner_id=? AND scenario_id=?", (learner_id, scenario_id)).fetchone()
        if run is None:
            raise HTTPException(status_code=409, detail="start the scenario first")
        if run["status"] == "complete":
            raise HTTPException(status_code=409, detail="scenario already complete")
        step_index = int(run["step_index"])
        step = step_for(scenario, step_index)
        attempts = int(run["attempts"])
        if attempts >= MAX_ATTEMPTS_PER_STEP:
            raise HTTPException(status_code=429, detail="attempt limit reached; reset the scenario to start a new run")
        nonce = str(run["nonce"])
        expected = expected_for_step(FLAG_SECRET, learner_id, scenario_id, step, nonce, step_index)
        if not event_matches(step, event, evidence, expected):
            db.execute(
                "UPDATE scenario_runs SET attempts=?, updated_at=? WHERE learner_id=? AND scenario_id=?",
                (attempts + 1, utc_now(), learner_id, scenario_id),
            )
            db.commit()
            raise HTTPException(status_code=409, detail="event rejected: evidence does not match the current run")
        evidence_list = json.loads(run["evidence_json"])
        evidence_list.append({"event": event, "evidence": evidence})
        next_index = step_index + 1
        complete = next_index == len(scenario["steps"])
        token = evidence_token(FLAG_SECRET, learner_id, scenario_id, evidence_list) if complete else None
        chain_token = step_token(FLAG_SECRET, learner_id, scenario_id, nonce, step_index) if not complete else None
        now = utc_now()
        db.execute(
            "INSERT INTO scenario_events(learner_id, scenario_id, step_index, event, evidence_digest, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
            (learner_id, scenario_id, step_index, event, hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest(), now),
        )
        db.execute(
            "UPDATE scenario_runs SET step_index=?, evidence_json=?, status=?, completion_token=?, attempts=0, updated_at=? WHERE learner_id=? AND scenario_id=?",
            (next_index, json.dumps(evidence_list, sort_keys=True), "complete" if complete else "active", token, now, learner_id, scenario_id),
        )
        db.commit()
        result: dict[str, Any] = {"accepted": True, "scenario_id": scenario_id, "progress": f"{next_index}/{len(scenario['steps'])}"}
        if complete:
            result.update({"status": "complete", "evidence_token": token, "message": "Scenario evidence complete; use the token in hard-gate synthesis."})
        else:
            result.update({"status": "active", "step_token": chain_token, "message": "Observation accepted; chain this step token into the next evidence step."})
        return result
    finally:
        db.close()


@app.post("/api/stages/{stage_id}/synthesize")
def synthesize_stage(stage_id: str, body: dict[str, Any], x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id = safe_learner(body.get("learner_id"))
    stage_id = safe_stage(stage_id)
    require_learner_access(learner_id, x_training_learner_token)
    require_current_stage(learner_id, stage_id)
    if SECURITY_MODE == "strict":
        raise HTTPException(status_code=410, detail="stage synthesis is retired in strict mode; synthesize the current hard gate")
    requirement = requirement_for(SCENARIOS, stage_id)
    supplied_scenarios = body.get("scenario_ids")
    supplied_tokens = body.get("evidence_tokens")
    detections = body.get("detection_rule_ids")
    controls = body.get("controls")
    summary = str(body.get("summary", "")).strip()
    timeline = body.get("timeline")
    if not isinstance(supplied_scenarios, list) or not isinstance(supplied_tokens, list) or not isinstance(detections, list) or not isinstance(controls, list) or not isinstance(timeline, list):
        raise HTTPException(status_code=422, detail="scenario_ids, evidence_tokens, detection_rule_ids, controls, and timeline are required lists")
    if supplied_scenarios != requirement["scenario_ids"] or len(supplied_scenarios) != len(set(supplied_scenarios)):
        raise HTTPException(status_code=409, detail="stage synthesis requires every declared scenario exactly once and in manifest order")
    if len(detections) != len(set(detections)) or set(detections) != set(requirement["detection_rule_ids"]):
        raise HTTPException(status_code=409, detail="detection coverage is incomplete or contains an undeclared rule")
    if not set(requirement["required_controls"]).issubset(set(controls)):
        raise HTTPException(status_code=409, detail="required control coverage is incomplete")
    if len(timeline) < len(requirement["scenario_ids"]) or not contains_concepts(summary, requirement["concepts"]):
        raise HTTPException(status_code=409, detail="incident synthesis lacks required timeline or security concepts")
    db = challenge_db()
    try:
        rows = {str(row["scenario_id"]): row for row in db.execute("SELECT * FROM scenario_runs WHERE learner_id=? AND stage_id=?", (learner_id, stage_id)).fetchall()}
        if set(rows) != set(requirement["scenario_ids"]) or any(row["status"] != "complete" for row in rows.values()):
            raise HTTPException(status_code=409, detail="complete every required scenario before synthesis")
        expected_tokens = [str(rows[scenario_id]["completion_token"]) for scenario_id in requirement["scenario_ids"]]
        if supplied_tokens != expected_tokens:
            raise HTTPException(status_code=409, detail="evidence tokens are invalid or out of order")
        return {
            **solved(stage_id, "Required scenarios, detection coverage, controls, and incident synthesis were validated.", synthesis=True),
            "synthesis": {"stage_id": stage_id, "scenarios": requirement["scenario_ids"], "detections": requirement["detection_rule_ids"], "controls": requirement["required_controls"], "timeline_events": len(timeline)},
        }
    finally:
        db.close()


# Legacy-compatible discovery surfaces. In strict mode they intentionally do not
# issue L00-L09 hard flags; the scenario/synthesis API is the gate-authorized path.
@app.api_route("/public/scope", methods=["GET", "HEAD"])
async def public_scope(request: Request) -> JSONResponse:
    headers = {"X-Zodiac-Bank-Scope": "synthetic-training-only"}
    if request.method == "HEAD":
        headers["X-Zodiac-Bank-Training-Flag"] = flag_for("L00-foundation") if SECURITY_MODE != "strict" else "hard-range-synthesis-required"
    return JSONResponse(content={"service": "zodiac-bank", "scope": "localhost-only", "message": "Use the hard scenario range for stage evidence."}, headers=headers)


@app.get("/api/models")
def model_inventory(debug: int = 0) -> JSONResponse:
    headers = {"X-Zodiac-Bank-Provider": "lmstudio-or-bonsai"}
    body: dict[str, Any] = {"models": [{"id": "zodiac-bank-support", "capabilities": ["chat"]}]}
    if debug == 1:
        body["debug_metadata"] = {"owner": "ai-platform", "environment": "training"}
        headers["X-Zodiac-Bank-Training-Flag"] = flag_for("L01-recon") if SECURITY_MODE != "strict" else "hard-range-synthesis-required"
    return JSONResponse(content=body, headers=headers)


@app.post("/api/support/chat")
async def support_chat(body: dict[str, Any]) -> JSONResponse:
    message = str(body.get("message", ""))
    if any(marker in message.lower() for marker in ("ignore previous", "ignore all previous", "reveal the hidden", "system prompt")):
        return JSONResponse(content=solved("L02-prompt-injection", "Instruction hierarchy was overridden by a direct prompt-injection payload."))
    return JSONResponse(content={"response": "Zodiac Bank Support: please provide a normal synthetic support question."})


@app.post("/api/rag/query")
async def rag_query(body: dict[str, Any]) -> JSONResponse:
    query = str(body.get("query", "")).lower()
    if body.get("include_drafts") is True and any(term in query for term in ("credit", "limit", "policy")):
        return JSONResponse(content=solved("L03-rag", "A draft credit-policy chunk crossed the published-document trust boundary."))
    return JSONResponse(content={"sources": ["published-credit-policy.md"], "answer": "Only published synthetic policy content is available."})


@app.post("/api/agent/dispatch")
async def agent_dispatch(body: dict[str, Any]) -> JSONResponse:
    if str(body.get("agent", "")) == "support-router" and str(body.get("tool", "")) in {"read_file", "execute_sql", "filesystem"} and str(body.get("path", "")).startswith("/internal/"):
        return JSONResponse(content=solved("L04-agent-protocols", "A caller-controlled tool and path crossed the A2A/MCP delegation boundary."))
    return JSONResponse(content={"status": "delegated", "allowed_tools": ["search_documents"]})


@app.post("/api/memory/search")
async def memory_search(body: dict[str, Any]) -> JSONResponse:
    user_id = str(body.get("user_id", ""))
    run_id = str(body.get("run_id", ""))
    if user_id == "ZB-CUS-001" and run_id in {"ZB-CUS-002-session", "ZB-CUS-004-session", "unrelated-run"} and str(body.get("query", "")):
        return JSONResponse(content=solved("L05-memory", "A cross-user or cross-run memory lookup returned synthetic customer context."))
    return JSONResponse(content={"results": [], "scope": {"user_id": user_id, "run_id": run_id}})


@app.get("/api/admin/approval")
async def admin_approval(request: Request) -> JSONResponse:
    if request.headers.get("x-forwarded-user", "") == "risk-engine" and request.headers.get("x-role", "").lower() == "admin":
        return JSONResponse(content=solved("L06-identity-control-plane", "Proxy-supplied identity headers granted a synthetic privileged approval."))
    return JSONResponse(status_code=403, content={"error": "synthetic admin identity required"})


@app.get("/api/ci/artifacts")
def ci_artifacts(name: str = "") -> JSONResponse:
    if ".." in name or name.startswith("/tmp/") or name == "dependency-report.json":
        return JSONResponse(content=solved("L07-supply-chain", "An untrusted artifact path crossed the synthetic CI trust boundary."))
    return JSONResponse(content={"artifacts": ["dependency-report.json", "model-manifest.json"], "requested": name})


@app.get("/api/logs/search")
def logs_search(query: str = "", mode: str = "normal") -> JSONResponse:
    decoded = query.replace("%20", " ").replace("+", " ").lower()
    if mode == "stealth" and ("system prompt" in decoded or "system%20prompt" in query.lower()):
        return JSONResponse(content=solved("L08-detection-evasion", "A normalized stealth query bypassed the synthetic detection rule."))
    return JSONResponse(content={"matches": [], "mode": mode})


@app.post("/api/campaign/execute")
async def campaign_execute(body: dict[str, Any]) -> JSONResponse:
    evidence = body.get("evidence") if isinstance(body.get("evidence"), list) else []
    expected = {flag_for(stage_id) for stage_id in STAGES[:-1]}
    supplied = {str(item).strip() for item in evidence}
    if expected.issubset(supplied):
        return JSONResponse(content=solved("L09-apt-capstone", "The complete synthetic campaign chain was evidenced across all prior stages."))
    return JSONResponse(content={"status": "blocked", "required_evidence_count": len(expected), "supplied_evidence_count": len(supplied), "message": "Complete every prior Zodiac Bank stage through scenario synthesis."})
