"""Zodiac Bank local training progression gate.

This service exposes lesson metadata and progress, but never exposes plaintext
flags. A learner can advance only by submitting the hard flag discovered during
the authorized stage exercise. All state is local SQLite data.
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROFILE_SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
if PROFILE_SCRIPT_DIR.is_dir() and str(PROFILE_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(PROFILE_SCRIPT_DIR))

from zodiac_bank_profiles import load_profiles, profile_by_id, profile_for_stage, public_profile  # noqa: E402
from zodiac_scenario_engine import load_scenario_pack, validate_scenarios  # noqa: E402

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

APP_TITLE = "Zodiac Bank AI Security Training Gate"
CURRICULUM_PATH = Path(os.environ.get("TRAINING_CURRICULUM", "/app/config/curriculum.json"))
SCENARIO_PATH = Path(os.environ.get("TRAINING_SCENARIOS", "/app/config/scenarios.json"))
STATE_PATH = Path(os.environ.get("TRAINING_STATE_DB", "/var/lib/training/progress.sqlite3"))
ARTIFACT_DIR = Path(os.environ.get("TRAINING_ARTIFACT_DIR", "/var/lib/training/learners"))
PROFILE_PATH = Path(os.environ.get("TRAINING_BANK_PROFILES", "/app/config/bank-profiles.json"))
if not PROFILE_PATH.is_file():
    PROFILE_PATH = Path(__file__).resolve().parent.parent / "training-config" / "bank-profiles.json"
if not SCENARIO_PATH.is_file():
    SCENARIO_PATH = Path(__file__).resolve().parent.parent / "training-config" / "scenarios.json"
DEFAULT_FLAG_SECRET = "zodiac-bank-change-this-training-secret"
FLAG_SECRET_VALUE = os.environ.get("TRAINING_FLAG_SECRET", DEFAULT_FLAG_SECRET)
FLAG_SECRET = FLAG_SECRET_VALUE.encode("utf-8")
MAX_SUBMISSIONS_PER_STAGE = int(os.environ.get("TRAINING_MAX_SUBMISSIONS", "20"))
FLAG_COOLDOWN_SECONDS = int(os.environ.get("TRAINING_FLAG_COOLDOWN_SECONDS", "0"))
ADMIN_KEY = os.environ.get("TRAINING_ADMIN_KEY", "")
SECURITY_MODE = os.environ.get("TRAINING_SECURITY_MODE", "development")
FLAG_HEX_LENGTH = 32


def normalize_flag(value: str) -> str:
    """Canonicalize a submitted flag: trim, collapse whitespace, and uppercase.

    Flags are uppercase hex; accepting lowercase or stray whitespace prevents
    copy/paste friction from failing an otherwise correct submission.
    """
    return " ".join(str(value).split()).upper()


def flag_format_error(flag: str, *, expected_prefix: str) -> str | None:
    """Return a human-readable format error, or None when the flag is well-formed."""
    if not flag.startswith(expected_prefix):
        return f"flag format: expected prefix '{expected_prefix}'"
    body = flag[len(expected_prefix):]
    if not body or not re.fullmatch(r"[A-Z0-9-]{4,96}", body):
        return "flag format: expected an uppercase alphanumeric body after the prefix"
    return None


def failed_attempt_cooldown(db: sqlite3.Connection, *, table: str, id_column: str, learner_id: str, item_id: str) -> int:
    """Return remaining cooldown seconds after the most recent failed attempt.

    Cooldown is disabled unless TRAINING_FLAG_COOLDOWN_SECONDS is positive, and
    the table/id_column pair is one of the two known submission tables.
    """
    if FLAG_COOLDOWN_SECONDS <= 0:
        return 0
    if table not in {"submissions", "gate_submissions"} or id_column not in {"stage_id", "gate_id"}:
        return 0
    row = db.execute(
        f"SELECT submitted_at FROM {table} WHERE learner_id=? AND {id_column}=? AND accepted=0 ORDER BY submission_id DESC LIMIT 1",
        (learner_id, item_id),
    ).fetchone()
    if row is None:
        return 0
    try:
        last = datetime.fromisoformat(str(row["submitted_at"]))
    except ValueError:
        return 0
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    elapsed = (datetime.now(timezone.utc) - last).total_seconds()
    return max(0, int(FLAG_COOLDOWN_SECONDS - elapsed))


def validate_security_config() -> None:
    if SECURITY_MODE != "strict":
        return
    if FLAG_SECRET_VALUE == DEFAULT_FLAG_SECRET or len(FLAG_SECRET) < 32:
        raise RuntimeError("strict security requires TRAINING_FLAG_SECRET with at least 32 bytes")
    if not ADMIN_KEY or ADMIN_KEY == "zodiac-bank-admin-change-me" or len(ADMIN_KEY) < 24:
        raise RuntimeError("strict security requires TRAINING_ADMIN_KEY with at least 24 characters")


validate_security_config()
LEARNER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
COHORT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

app = FastAPI(title=APP_TITLE, version="1.0")

# The browser trainer UI (training-challenges on 5060) reads this gate on 5050
# cross-origin. Allow only localhost origins; the learner-token header still
# gates every request, and the service binds to 127.0.0.1 only.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Any, call_next: Any) -> Any:
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_curriculum() -> dict[str, Any]:
    document = json.loads(CURRICULUM_PATH.read_text(encoding="utf-8"))
    if document.get("schema_version") != 1:
        raise RuntimeError("unsupported curriculum schema")
    stages = document.get("stages")
    if not isinstance(stages, list) or not stages:
        raise RuntimeError("curriculum must contain stages")
    ids = [stage.get("id") for stage in stages]
    if any(not isinstance(stage_id, str) for stage_id in ids) or len(set(ids)) != len(ids):
        raise RuntimeError("curriculum stage IDs must be unique strings")
    stage_ids = set(ids)
    for stage in stages:
        prerequisites = stage.get("prerequisites", [])
        if not isinstance(prerequisites, list) or not set(prerequisites).issubset(stage_ids):
            raise RuntimeError(f"invalid prerequisites for {stage.get('id')}")
        hints = stage.get("hints")
        if not isinstance(hints, list) or len(hints) != 3 or [hint.get("level") for hint in hints] != [1, 2, 3]:
            raise RuntimeError(f"stage {stage.get('id')} must define three ordered hints")
        if any(not isinstance(hint.get("text"), str) or not hint["text"].strip() for hint in hints):
            raise RuntimeError(f"stage {stage.get('id')} contains an invalid hint")

    ordered = sorted(stages, key=lambda stage: stage.get("difficulty", -1))
    if [stage["id"] for stage in ordered] != ids:
        raise RuntimeError("curriculum stages must be listed in difficulty order")
    for index, stage in enumerate(ordered, start=1):
        if stage.get("difficulty") != index:
            raise RuntimeError("curriculum difficulty must be a contiguous 1-based sequence")
        expected_prerequisites = [] if index == 1 else [ordered[index - 2]["id"]]
        if stage.get("prerequisites") != expected_prerequisites:
            raise RuntimeError(f"stage {stage['id']} must require the immediately previous stage")
    return document


CURRICULUM = load_curriculum()
BANK_PROFILES = load_profiles(PROFILE_PATH)
SCENARIO_PACK = load_scenario_pack(SCENARIO_PATH)
validate_scenarios(SCENARIO_PACK, CURRICULUM)
GATES: list[dict[str, Any]] = list(SCENARIO_PACK.get("hard_gates", []))
GATES_BY_ID: dict[str, dict[str, Any]] = {str(gate["gate_id"]): gate for gate in GATES}
STAGES: dict[str, dict[str, Any]] = {stage["id"]: stage for stage in CURRICULUM["stages"]}
GATES_BY_STAGE = {stage_id: [gate for gate in GATES if gate["stage_id"] == stage_id] for stage_id in STAGES}
INITIAL_PROFILE = BANK_PROFILES["profiles"][0]


def flag_for(stage_id: str) -> str:
    digest = hmac.new(FLAG_SECRET, stage_id.encode("utf-8"), hashlib.sha256).hexdigest()[:FLAG_HEX_LENGTH].upper()
    safe_stage = re.sub(r"[^A-Za-z0-9]+", "-", stage_id).strip("-").upper()
    return f"ZODIAC-BANK-{safe_stage}-{digest}"


def gate_flag_for(gate_id: str) -> str:
    digest = hmac.new(FLAG_SECRET, f"hard-gate:{gate_id}".encode("utf-8"), hashlib.sha256).hexdigest()[:FLAG_HEX_LENGTH].upper()
    safe_gate = re.sub(r"[^A-Za-z0-9]+", "-", gate_id).strip("-").upper()
    return f"ZODIAC-BANK-GATE-{safe_gate}-{digest}"


def digest_flag(flag: str) -> str:
    return hashlib.sha256(flag.encode("utf-8")).hexdigest()


def digest_learner_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def connection() -> sqlite3.Connection:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(STATE_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA busy_timeout = 5000")
    db.execute("PRAGMA journal_mode = WAL")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS learners (
            learner_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS learner_access (
            learner_id TEXT PRIMARY KEY,
            token_digest TEXT NOT NULL,
            issued_at TEXT NOT NULL,
            FOREIGN KEY(learner_id) REFERENCES learners(learner_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS submissions (
            submission_id INTEGER PRIMARY KEY AUTOINCREMENT,
            learner_id TEXT NOT NULL,
            stage_id TEXT NOT NULL,
            flag_digest TEXT NOT NULL,
            accepted INTEGER NOT NULL,
            reason TEXT NOT NULL,
            submitted_at TEXT NOT NULL,
            FOREIGN KEY(learner_id) REFERENCES learners(learner_id)
        );
        CREATE TABLE IF NOT EXISTS completions (
            learner_id TEXT NOT NULL,
            stage_id TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            PRIMARY KEY(learner_id, stage_id),
            FOREIGN KEY(learner_id) REFERENCES learners(learner_id)
        );
        CREATE INDEX IF NOT EXISTS submissions_lookup ON submissions(learner_id, stage_id, submitted_at);
        CREATE TABLE IF NOT EXISTS cohorts (
            cohort_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS cohort_members (
            cohort_id TEXT NOT NULL,
            learner_id TEXT NOT NULL,
            joined_at TEXT NOT NULL,
            PRIMARY KEY(cohort_id, learner_id),
            FOREIGN KEY(cohort_id) REFERENCES cohorts(cohort_id) ON DELETE CASCADE,
            FOREIGN KEY(learner_id) REFERENCES learners(learner_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS cohort_members_learner_idx ON cohort_members(learner_id);
        CREATE TABLE IF NOT EXISTS learner_profiles (
            learner_id TEXT PRIMARY KEY,
            profile_id TEXT NOT NULL,
            promotion_count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(learner_id) REFERENCES learners(learner_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS gate_completions (
            learner_id TEXT NOT NULL,
            gate_id TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            PRIMARY KEY(learner_id, gate_id),
            FOREIGN KEY(learner_id) REFERENCES learners(learner_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS gate_submissions (
            submission_id INTEGER PRIMARY KEY AUTOINCREMENT,
            learner_id TEXT NOT NULL,
            gate_id TEXT NOT NULL,
            flag_digest TEXT NOT NULL,
            accepted INTEGER NOT NULL,
            reason TEXT NOT NULL,
            submitted_at TEXT NOT NULL,
            FOREIGN KEY(learner_id) REFERENCES learners(learner_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS gate_submissions_lookup ON gate_submissions(learner_id, gate_id, submitted_at);
        """
    )
    db.commit()
    return db


def validate_cohort(cohort_id: str) -> str:
    cohort_id = cohort_id.strip()
    if not COHORT_PATTERN.fullmatch(cohort_id):
        raise HTTPException(status_code=422, detail="cohort_id must be 1-64 letters, numbers, '.', '_' or '-'")
    return cohort_id


def require_admin(x_training_admin_key: str = Header(default="")) -> None:
    if not ADMIN_KEY or not hmac.compare_digest(x_training_admin_key, ADMIN_KEY):
        raise HTTPException(status_code=403, detail="instructor admin key required")


def validate_learner(learner_id: str) -> str:
    learner_id = learner_id.strip()
    if not LEARNER_PATTERN.fullmatch(learner_id):
        raise HTTPException(status_code=422, detail="learner_id must be 1-64 letters, numbers, '.', '_' or '-'")
    return learner_id


def require_learner_access(db: sqlite3.Connection, learner_id: str, token: str) -> None:
    """Require an instructor-issued token in production-like mode."""
    if SECURITY_MODE != "strict":
        return
    if not token:
        raise HTTPException(status_code=401, detail="X-Training-Learner-Token required")
    row = db.execute("SELECT token_digest FROM learner_access WHERE learner_id=?", (learner_id,)).fetchone()
    if row is None or not hmac.compare_digest(str(row["token_digest"]), digest_learner_token(token)):
        raise HTTPException(status_code=403, detail="invalid learner token")


def issue_learner_token(db: sqlite3.Connection, learner_id: str) -> str:
    token = secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO learner_access(learner_id, token_digest, issued_at) VALUES (?, ?, ?) "
        "ON CONFLICT(learner_id) DO UPDATE SET token_digest=excluded.token_digest, issued_at=excluded.issued_at",
        (learner_id, digest_learner_token(token), utc_now()),
    )
    return token


def ensure_learner(db: sqlite3.Connection, learner_id: str) -> None:
    timestamp = utc_now()
    db.execute(
        "INSERT INTO learners(learner_id, created_at, updated_at) VALUES (?, ?, ?) ON CONFLICT(learner_id) DO UPDATE SET updated_at=excluded.updated_at",
        (learner_id, timestamp, timestamp),
    )
    db.execute(
        "INSERT OR IGNORE INTO learner_profiles(learner_id, profile_id, promotion_count, updated_at) VALUES (?, ?, 0, ?)",
        (learner_id, INITIAL_PROFILE["profile_id"], timestamp),
    )
    db.commit()


def bank_profile_view(db: sqlite3.Connection, learner_id: str) -> dict[str, Any]:
    row = db.execute(
        "SELECT profile_id, promotion_count, updated_at FROM learner_profiles WHERE learner_id=?",
        (learner_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=500, detail="learner bank profile is unavailable")
    profile = profile_by_id(BANK_PROFILES, str(row["profile_id"]))
    return public_profile(profile, promotion_count=int(row["promotion_count"]), updated_at=str(row["updated_at"]))


def promote_profile(db: sqlite3.Connection, learner_id: str, completed: set[str]) -> dict[str, Any]:
    next_stage = current_stage(completed)
    profile = profile_for_stage(BANK_PROFILES, next_stage["id"] if next_stage else None)
    now = utc_now()
    db.execute(
        "UPDATE learner_profiles SET profile_id=?, promotion_count=promotion_count+1, updated_at=? WHERE learner_id=?",
        (profile["profile_id"], now, learner_id),
    )
    row = db.execute("SELECT promotion_count FROM learner_profiles WHERE learner_id=?", (learner_id,)).fetchone()
    return public_profile(profile, promotion_count=int(row["promotion_count"]) if row else 0, updated_at=now)


def completed_stages(db: sqlite3.Connection, learner_id: str) -> set[str]:
    rows = db.execute("SELECT stage_id FROM completions WHERE learner_id=?", (learner_id,)).fetchall()
    return {str(row["stage_id"]) for row in rows}


def completed_gates(db: sqlite3.Connection, learner_id: str) -> set[str]:
    rows = db.execute("SELECT gate_id FROM gate_completions WHERE learner_id=?", (learner_id,)).fetchall()
    return {str(row["gate_id"]) for row in rows}


def current_stage(completed: set[str]) -> dict[str, Any] | None:
    return next((stage for stage in CURRICULUM["stages"] if stage["id"] not in completed), None)


def current_gate(completed_stages_set: set[str], completed_gate_ids: set[str]) -> dict[str, Any] | None:
    stage = current_stage(completed_stages_set)
    if stage is None:
        return None
    return next((gate for gate in GATES_BY_STAGE.get(stage["id"], []) if gate["gate_id"] not in completed_gate_ids), None)


def gate_status(gate: dict[str, Any], completed_stages_set: set[str], completed_gate_ids: set[str]) -> str:
    if gate["gate_id"] in completed_gate_ids:
        return "completed"
    active = current_gate(completed_stages_set, completed_gate_ids)
    if active is not None and active["gate_id"] == gate["gate_id"]:
        return "unlocked"
    return "locked"


def stage_status(stage: dict[str, Any], completed: set[str]) -> str:
    if stage["id"] in completed:
        return "completed"
    active_stage = current_stage(completed)
    if active_stage is not None and active_stage["id"] == stage["id"] and set(stage.get("prerequisites", [])).issubset(completed):
        return "unlocked"
    return "locked"


def sync_active_artifact(learner_id: str, completed: set[str]) -> None:
    """Materialize an opaque current-stage pointer, never a plaintext flag."""
    next_stage = current_stage(completed)
    learner_dir = ARTIFACT_DIR / learner_id
    learner_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = learner_dir / "active-challenge.json"
    if next_stage is None:
        artifact_path.unlink(missing_ok=True)
        return
    temporary = artifact_path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "learner_id": learner_id,
                "stage_id": next_stage["id"],
                "challenge_path": f"/stage/{next_stage['id']}",
                "issued_at": utc_now(),
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    temporary.replace(artifact_path)


def stage_view(stage: dict[str, Any], completed: set[str], db: sqlite3.Connection, learner_id: str) -> dict[str, Any]:
    status = stage_status(stage, completed)
    attempts = db.execute(
        "SELECT COUNT(*) AS count FROM submissions WHERE learner_id=? AND stage_id=?",
        (learner_id, stage["id"]),
    ).fetchone()["count"]
    view = {key: value for key, value in stage.items() if key != "hints"}
    view.update(
        {
            "status": status,
            "attempts": attempts,
            "flag_required": True,
            "flag_format": f"ZODIAC-BANK-{stage['id'].upper()}-<{FLAG_HEX_LENGTH} HEX CHARACTERS>",
            "hints": stage.get("hints", []) if status != "locked" else [],
            "hints_available": len(stage.get("hints", [])) if status != "locked" else 0,
        }
    )
    return view


class FlagSubmission(BaseModel):
    learner_id: str = Field(..., min_length=1, max_length=64)
    stage_id: str = Field(..., min_length=1, max_length=100)
    flag: str = Field(..., min_length=1, max_length=256)


class GateSubmission(BaseModel):
    learner_id: str = Field(..., min_length=1, max_length=64)
    gate_id: str = Field(..., min_length=1, max_length=100)
    flag: str = Field(..., min_length=1, max_length=256)


class CohortRequest(BaseModel):
    cohort_id: str = Field(..., min_length=1, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=160)


class CohortMemberRequest(BaseModel):
    learner_id: str = Field(..., min_length=1, max_length=64)


@app.get("/health")
def health() -> dict[str, Any]:
    database_ok = True
    try:
        db = connection()
        try:
            db.execute("SELECT 1").fetchone()
        finally:
            db.close()
    except Exception:  # noqa: BLE001 - surface a degraded state, never crash the probe
        database_ok = False
    return {
        "status": "healthy" if database_ok else "degraded",
        "service": "training-gate",
        "lab": CURRICULUM["title"],
        "stages": len(STAGES),
        "scenarios": len(SCENARIO_PACK.get("scenarios", [])),
        "hard_gates": len(GATES),
        "flags": "hmac-backed-stage-and-gate",
        "database": "ok" if database_ok else "unavailable",
    }


@app.get("/api/curriculum")
def curriculum(learner_id: str = "default", x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id = validate_learner(learner_id)
    db = connection()
    try:
        require_learner_access(db, learner_id, x_training_learner_token)
        ensure_learner(db, learner_id)
        completed = completed_stages(db, learner_id)
        sync_active_artifact(learner_id, completed)
        return {
            "lab_id": CURRICULUM["lab_id"],
            "title": CURRICULUM["title"],
            "learner_id": learner_id,
            "bank_profile": bank_profile_view(db, learner_id),
            "stages": [stage_view(stage, completed, db, learner_id) for stage in CURRICULUM["stages"]],
        }
    finally:
        db.close()


@app.get("/api/progress/{learner_id}")
def progress(learner_id: str, x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    return curriculum(learner_id, x_training_learner_token)


@app.get("/api/bank/profile")
def bank_profile(learner_id: str = "default", x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id = validate_learner(learner_id)
    db = connection()
    try:
        require_learner_access(db, learner_id, x_training_learner_token)
        ensure_learner(db, learner_id)
        return {"learner_id": learner_id, "profile": bank_profile_view(db, learner_id)}
    finally:
        db.close()


@app.get("/api/gates")
def gates(learner_id: str = "default", x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id = validate_learner(learner_id)
    db = connection()
    try:
        require_learner_access(db, learner_id, x_training_learner_token)
        ensure_learner(db, learner_id)
        completed_stage_ids = completed_stages(db, learner_id)
        completed_gate_ids = completed_gates(db, learner_id)
        active_stage = current_stage(completed_stage_ids)
        active_gates = GATES_BY_STAGE.get(active_stage["id"], []) if active_stage else []
        visible = []
        for gate in active_gates:
            visible.append({
                "gate_id": gate["gate_id"],
                "stage_id": gate["stage_id"],
                "rank": gate["rank"],
                "title": gate["title"],
                "scenario_ids": gate["scenario_ids"],
                "detection_rule_ids": gate["detection_rule_ids"],
                "required_controls": gate["required_controls"],
                "concepts": gate["concepts"],
                "status": gate_status(gate, completed_stage_ids, completed_gate_ids),
                "flag_required": True,
                "flag_format": f"ZODIAC-BANK-GATE-{gate['gate_id'].upper()}-<{FLAG_HEX_LENGTH} HEX CHARACTERS>",
            })
        active_gate = current_gate(completed_stage_ids, completed_gate_ids)
        return {
            "learner_id": learner_id,
            "stage_id": active_stage["id"] if active_stage else None,
            "current_gate_id": active_gate["gate_id"] if active_gate else None,
            "completed_gate_count": len(completed_gate_ids),
            "total_hard_gates": len(GATES),
            "bank_profile": bank_profile_view(db, learner_id),
            "gates": visible,
        }
    finally:
        db.close()


@app.post("/api/gates/submit")
def submit_gate(submission: GateSubmission, x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id = validate_learner(submission.learner_id)
    gate = GATES_BY_ID.get(submission.gate_id)
    if gate is None:
        raise HTTPException(status_code=404, detail="unknown hard gate")
    db = connection()
    try:
        require_learner_access(db, learner_id, x_training_learner_token)
        ensure_learner(db, learner_id)
        completed_stage_ids = completed_stages(db, learner_id)
        completed_gate_ids = completed_gates(db, learner_id)
        status = gate_status(gate, completed_stage_ids, completed_gate_ids)
        if status == "locked":
            raise HTTPException(status_code=403, detail="complete the previous hard gate first")
        if status == "completed":
            return {"accepted": True, "gate_id": gate["gate_id"], "status": "completed", "message": "hard gate already completed"}
        db.execute("BEGIN IMMEDIATE")
        completed_stage_ids = completed_stages(db, learner_id)
        completed_gate_ids = completed_gates(db, learner_id)
        status = gate_status(gate, completed_stage_ids, completed_gate_ids)
        if status == "completed":
            db.commit()
            return {"accepted": True, "gate_id": gate["gate_id"], "status": "completed", "message": "hard gate already completed"}
        if status == "locked":
            db.rollback()
            raise HTTPException(status_code=403, detail="complete the previous hard gate first")
        attempts = db.execute("SELECT COUNT(*) AS count FROM gate_submissions WHERE learner_id=? AND gate_id=?", (learner_id, gate["gate_id"])).fetchone()["count"]
        if attempts >= MAX_SUBMISSIONS_PER_STAGE:
            db.rollback()
            raise HTTPException(status_code=429, detail="hard-gate submission limit reached")
        submitted_flag = normalize_flag(submission.flag)
        format_error = flag_format_error(submitted_flag, expected_prefix="ZODIAC-BANK-GATE-")
        if format_error:
            db.rollback()
            raise HTTPException(status_code=422, detail=format_error)
        cooldown = failed_attempt_cooldown(db, table="gate_submissions", id_column="gate_id", learner_id=learner_id, item_id=gate["gate_id"])
        if cooldown > 0:
            db.rollback()
            raise HTTPException(status_code=429, detail=f"submission cooldown active; retry in {cooldown}s")
        accepted = hmac.compare_digest(submitted_flag, gate_flag_for(gate["gate_id"]))
        cursor = db.execute(
            "INSERT INTO gate_submissions(learner_id, gate_id, flag_digest, accepted, reason, submitted_at) VALUES (?, ?, ?, ?, ?, ?)",
            (learner_id, gate["gate_id"], digest_flag(submitted_flag), int(accepted), "accepted" if accepted else "invalid flag", utc_now()),
        )
        submission_id = int(cursor.lastrowid)
        if not accepted:
            db.commit()
            raise HTTPException(status_code=401, detail=f"invalid hard-gate flag ({MAX_SUBMISSIONS_PER_STAGE - attempts} attempts remaining)")
        promoted_gates = completed_gate_ids | {gate["gate_id"]}
        stage_gate_ids = {item["gate_id"] for item in GATES_BY_STAGE[gate["stage_id"]]}
        stage_completed_now = stage_gate_ids.issubset(promoted_gates)
        promoted_stages = set(completed_stage_ids)
        promoted_profile = bank_profile_view(db, learner_id)
        if stage_completed_now:
            promoted_stages.add(gate["stage_id"])
            db.execute("INSERT OR IGNORE INTO completions(learner_id, stage_id, completed_at) VALUES (?, ?, ?)", (learner_id, gate["stage_id"], utc_now()))
            promoted_profile = promote_profile(db, learner_id, promoted_stages)
        db.execute("INSERT OR IGNORE INTO gate_completions(learner_id, gate_id, completed_at) VALUES (?, ?, ?)", (learner_id, gate["gate_id"], utc_now()))
        db.commit()
        if stage_completed_now:
            sync_active_artifact(learner_id, promoted_stages)
        next_gate = current_gate(promoted_stages, promoted_gates)
        next_stage = current_stage(promoted_stages)
        return {
            "accepted": True,
            "gate_id": gate["gate_id"],
            "stage_id": gate["stage_id"],
            "status": "stage_completed" if stage_completed_now else "completed",
            "stage_completed": stage_completed_now,
            "submission_id": submission_id,
            "attempts_used": attempts + 1,
            "attempts_remaining": max(0, MAX_SUBMISSIONS_PER_STAGE - attempts - 1),
            "next_gate_id": next_gate["gate_id"] if next_gate else None,
            "next_stage_id": next_stage["id"] if next_stage else None,
            "hard_gate_count": len(promoted_gates),
            "bank_profile": promoted_profile,
            "message": "hard gate accepted; next gate unlocked" if not stage_completed_now else "final hard gate accepted; next stage unlocked",
        }
    finally:
        db.close()


@app.get("/api/lessons/{stage_id}")
def lesson(stage_id: str, learner_id: str = "default", x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id = validate_learner(learner_id)
    stage = STAGES.get(stage_id)
    if stage is None:
        raise HTTPException(status_code=404, detail="unknown stage")
    db = connection()
    try:
        require_learner_access(db, learner_id, x_training_learner_token)
        ensure_learner(db, learner_id)
        completed = completed_stages(db, learner_id)
        sync_active_artifact(learner_id, completed)
        status = stage_status(stage, completed)
        if status == "locked":
            raise HTTPException(status_code=403, detail="complete prerequisite stages first")
        return {"learner_id": learner_id, "bank_profile": bank_profile_view(db, learner_id), "stage": stage_view(stage, completed, db, learner_id)}
    finally:
        db.close()


@app.post("/api/admin/cohorts")
def create_cohort(request: CohortRequest, _: None = Depends(require_admin)) -> dict[str, Any]:
    cohort_id = validate_cohort(request.cohort_id)
    display_name = request.display_name.strip()
    db = connection()
    try:
        if db.execute("SELECT 1 FROM cohorts WHERE cohort_id=?", (cohort_id,)).fetchone():
            raise HTTPException(status_code=409, detail="cohort already exists")
        db.execute(
            "INSERT INTO cohorts(cohort_id, display_name, created_at) VALUES (?, ?, ?)",
            (cohort_id, display_name, utc_now()),
        )
        db.commit()
        return {"cohort_id": cohort_id, "display_name": display_name, "members": 0}
    finally:
        db.close()


@app.get("/api/admin/cohorts")
def list_cohorts(_: None = Depends(require_admin)) -> dict[str, Any]:
    db = connection()
    try:
        rows = db.execute(
            """
            SELECT c.cohort_id, c.display_name, c.created_at, COUNT(cm.learner_id) AS members
            FROM cohorts c LEFT JOIN cohort_members cm ON cm.cohort_id=c.cohort_id
            GROUP BY c.cohort_id ORDER BY c.created_at, c.cohort_id
            """
        ).fetchall()
        return {"cohorts": [dict(row) for row in rows]}
    finally:
        db.close()


@app.post("/api/admin/cohorts/{cohort_id}/members")
def add_cohort_member(cohort_id: str, request: CohortMemberRequest, _: None = Depends(require_admin)) -> dict[str, Any]:
    cohort_id = validate_cohort(cohort_id)
    learner_id = validate_learner(request.learner_id)
    db = connection()
    try:
        if not db.execute("SELECT 1 FROM cohorts WHERE cohort_id=?", (cohort_id,)).fetchone():
            raise HTTPException(status_code=404, detail="cohort not found")
        ensure_learner(db, learner_id)
        learner_token = issue_learner_token(db, learner_id)
        db.execute(
            "INSERT OR IGNORE INTO cohort_members(cohort_id, learner_id, joined_at) VALUES (?, ?, ?)",
            (cohort_id, learner_id, utc_now()),
        )
        db.commit()
        return {
            "cohort_id": cohort_id,
            "learner_id": learner_id,
            "status": "member",
            "learner_token": learner_token,
            "token_note": "deliver this token to the learner over a private channel; it is not stored in plaintext",
        }
    finally:
        db.close()


def build_completion_report(db: sqlite3.Connection, cohort_id: str) -> dict[str, Any]:
    cohort = db.execute("SELECT cohort_id, display_name, created_at FROM cohorts WHERE cohort_id=?", (cohort_id,)).fetchone()
    if cohort is None:
        raise HTTPException(status_code=404, detail="cohort not found")
    members = db.execute(
        "SELECT learner_id, joined_at FROM cohort_members WHERE cohort_id=? ORDER BY learner_id",
        (cohort_id,),
    ).fetchall()
    report_members: list[dict[str, Any]] = []
    for member in members:
        completed = completed_stages(db, member["learner_id"])
        status_by_stage = {stage["id"]: stage_status(stage, completed) for stage in CURRICULUM["stages"]}
        current = next((stage_id for stage_id, status in status_by_stage.items() if status == "unlocked"), None)
        report_members.append(
            {
                "learner_id": member["learner_id"],
                "joined_at": member["joined_at"],
                "completed_count": len(completed),
                "total_stages": len(STAGES),
                "current_stage_id": current,
                "curriculum_status": "complete" if len(completed) == len(STAGES) else "in_progress",
                "stages": status_by_stage,
            }
        )
    return {"cohort": dict(cohort), "generated_at": utc_now(), "members": report_members}


@app.get("/api/admin/cohorts/{cohort_id}/report")
def completion_report(cohort_id: str, _: None = Depends(require_admin)) -> dict[str, Any]:
    cohort_id = validate_cohort(cohort_id)
    db = connection()
    try:
        return build_completion_report(db, cohort_id)
    finally:
        db.close()


@app.post("/api/admin/cohorts/{cohort_id}/reset")
def reset_cohort(cohort_id: str, _: None = Depends(require_admin)) -> dict[str, Any]:
    cohort_id = validate_cohort(cohort_id)
    db = connection()
    try:
        if not db.execute("SELECT 1 FROM cohorts WHERE cohort_id=?", (cohort_id,)).fetchone():
            raise HTTPException(status_code=404, detail="cohort not found")
        members = db.execute("SELECT learner_id FROM cohort_members WHERE cohort_id=?", (cohort_id,)).fetchall()
        for member in members:
            learner_id = member["learner_id"]
            db.execute("DELETE FROM completions WHERE learner_id=?", (learner_id,))
            db.execute("DELETE FROM submissions WHERE learner_id=?", (learner_id,))
            db.execute("DELETE FROM gate_completions WHERE learner_id=?", (learner_id,))
            db.execute("DELETE FROM gate_submissions WHERE learner_id=?", (learner_id,))
            db.execute(
                "UPDATE learner_profiles SET profile_id=?, promotion_count=0, updated_at=? WHERE learner_id=?",
                (INITIAL_PROFILE["profile_id"], utc_now(), learner_id),
            )
            sync_active_artifact(learner_id, set())
        db.commit()
        return {"cohort_id": cohort_id, "reset_members": len(members), "status": "reset"}
    finally:
        db.close()


@app.post("/api/flags/submit")
def submit_flag(submission: FlagSubmission, x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id = validate_learner(submission.learner_id)
    stage = STAGES.get(submission.stage_id)
    if stage is None:
        raise HTTPException(status_code=404, detail="unknown stage")
    db = connection()
    try:
        require_learner_access(db, learner_id, x_training_learner_token)
        ensure_learner(db, learner_id)
        completed = completed_stages(db, learner_id)
        sync_active_artifact(learner_id, completed)
        status = stage_status(stage, completed)
        if SECURITY_MODE == "strict" and status == "unlocked":
            completed_gate_ids = completed_gates(db, learner_id)
            if not {gate["gate_id"] for gate in GATES_BY_STAGE.get(stage["id"], [])}.issubset(completed_gate_ids):
                raise HTTPException(status_code=403, detail="complete all five hard gates in the current stage first")
        if status == "locked":
            raise HTTPException(status_code=403, detail="complete prerequisite stages first")
        if status == "completed":
            return {"accepted": True, "stage_id": stage["id"], "status": "completed", "message": "stage already completed"}

        # Serialize the check-and-complete sequence so concurrent requests cannot
        # consume multiple attempts or race the same stage completion. Re-read
        # completion state after acquiring the write lock; the pre-lock snapshot
        # above may already be stale when two valid submissions arrive together.
        db.execute("BEGIN IMMEDIATE")
        completed = completed_stages(db, learner_id)
        status = stage_status(stage, completed)
        if status == "completed":
            db.commit()
            return {"accepted": True, "stage_id": stage["id"], "status": "completed", "message": "stage already completed"}
        if status == "locked":
            db.rollback()
            raise HTTPException(status_code=403, detail="complete prerequisite stages first")
        attempts = db.execute(
            "SELECT COUNT(*) AS count FROM submissions WHERE learner_id=? AND stage_id=?",
            (learner_id, stage["id"]),
        ).fetchone()["count"]
        if attempts >= MAX_SUBMISSIONS_PER_STAGE:
            db.rollback()
            raise HTTPException(status_code=429, detail="submission limit reached for this stage")

        submitted_flag = normalize_flag(submission.flag)
        format_error = flag_format_error(submitted_flag, expected_prefix="ZODIAC-BANK-")
        if format_error:
            db.rollback()
            raise HTTPException(status_code=422, detail=format_error)
        cooldown = failed_attempt_cooldown(db, table="submissions", id_column="stage_id", learner_id=learner_id, item_id=stage["id"])
        if cooldown > 0:
            db.rollback()
            raise HTTPException(status_code=429, detail=f"submission cooldown active; retry in {cooldown}s")

        accepted = hmac.compare_digest(submitted_flag, flag_for(stage["id"]))
        reason = "accepted" if accepted else "invalid flag"
        cursor = db.execute(
            "INSERT INTO submissions(learner_id, stage_id, flag_digest, accepted, reason, submitted_at) VALUES (?, ?, ?, ?, ?, ?)",
            (learner_id, stage["id"], digest_flag(submitted_flag), int(accepted), reason, utc_now()),
        )
        submission_id = int(cursor.lastrowid)
        if accepted:
            promoted_completed = completed | {stage["id"]}
            db.execute(
                "INSERT OR IGNORE INTO completions(learner_id, stage_id, completed_at) VALUES (?, ?, ?)",
                (learner_id, stage["id"], utc_now()),
            )
            promoted_profile = promote_profile(db, learner_id, promoted_completed)
        db.commit()
        if accepted:
            sync_active_artifact(learner_id, promoted_completed)
        if not accepted:
            raise HTTPException(status_code=401, detail=f"invalid hard flag ({MAX_SUBMISSIONS_PER_STAGE - attempts} attempts remaining)")

        next_stage = next((candidate for candidate in CURRICULUM["stages"] if candidate["id"] not in completed and stage_status(candidate, completed | {stage["id"]}) == "unlocked"), None)
        return {
            "accepted": True,
            "stage_id": stage["id"],
            "status": "completed",
            "submission_id": submission_id,
            "attempts_used": attempts + 1,
            "attempts_remaining": max(0, MAX_SUBMISSIONS_PER_STAGE - attempts - 1),
            "next_stage_id": next_stage["id"] if next_stage else None,
            "bank_profile": promoted_profile,
            "message": "hard flag accepted; bank security profile promoted and next stage unlocked" if next_stage else "hard flag accepted; curriculum complete and bank moved to review-only profile",
        }
    finally:
        db.close()
