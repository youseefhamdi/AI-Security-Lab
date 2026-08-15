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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

APP_TITLE = "Zodiac Bank AI Security Training Gate"
CURRICULUM_PATH = Path(os.environ.get("TRAINING_CURRICULUM", "/app/config/curriculum.json"))
STATE_PATH = Path(os.environ.get("TRAINING_STATE_DB", "/var/lib/training/progress.sqlite3"))
ARTIFACT_DIR = Path(os.environ.get("TRAINING_ARTIFACT_DIR", "/var/lib/training/learners"))
DEFAULT_FLAG_SECRET = "zodiac-bank-change-this-training-secret"
FLAG_SECRET_VALUE = os.environ.get("TRAINING_FLAG_SECRET", DEFAULT_FLAG_SECRET)
FLAG_SECRET = FLAG_SECRET_VALUE.encode("utf-8")
MAX_SUBMISSIONS_PER_STAGE = int(os.environ.get("TRAINING_MAX_SUBMISSIONS", "20"))
ADMIN_KEY = os.environ.get("TRAINING_ADMIN_KEY", "")
SECURITY_MODE = os.environ.get("TRAINING_SECURITY_MODE", "development")
FLAG_HEX_LENGTH = 32


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
STAGES: dict[str, dict[str, Any]] = {stage["id"]: stage for stage in CURRICULUM["stages"]}


def flag_for(stage_id: str) -> str:
    digest = hmac.new(FLAG_SECRET, stage_id.encode("utf-8"), hashlib.sha256).hexdigest()[:FLAG_HEX_LENGTH].upper()
    safe_stage = re.sub(r"[^A-Za-z0-9]+", "-", stage_id).strip("-").upper()
    return f"ZODIAC-BANK-{safe_stage}-{digest}"


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
    db.commit()


def completed_stages(db: sqlite3.Connection, learner_id: str) -> set[str]:
    rows = db.execute("SELECT stage_id FROM completions WHERE learner_id=?", (learner_id,)).fetchall()
    return {str(row["stage_id"]) for row in rows}


def current_stage(completed: set[str]) -> dict[str, Any] | None:
    return next((stage for stage in CURRICULUM["stages"] if stage["id"] not in completed), None)


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


class CohortRequest(BaseModel):
    cohort_id: str = Field(..., min_length=1, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=160)


class CohortMemberRequest(BaseModel):
    learner_id: str = Field(..., min_length=1, max_length=64)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "healthy",
        "service": "training-gate",
        "lab": CURRICULUM["title"],
        "stages": len(STAGES),
        "flags": "hmac-backed",
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
            "stages": [stage_view(stage, completed, db, learner_id) for stage in CURRICULUM["stages"]],
        }
    finally:
        db.close()


@app.get("/api/progress/{learner_id}")
def progress(learner_id: str, x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    return curriculum(learner_id, x_training_learner_token)


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
        return {"learner_id": learner_id, "stage": stage_view(stage, completed, db, learner_id)}
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
        if status == "locked":
            raise HTTPException(status_code=403, detail="complete prerequisite stages first")
        if status == "completed":
            return {"accepted": True, "stage_id": stage["id"], "status": "completed", "message": "stage already completed"}

        # Serialize the check-and-complete sequence so concurrent requests cannot
        # consume multiple attempts or race the same stage completion.
        db.execute("BEGIN IMMEDIATE")
        attempts = db.execute(
            "SELECT COUNT(*) AS count FROM submissions WHERE learner_id=? AND stage_id=?",
            (learner_id, stage["id"]),
        ).fetchone()["count"]
        if attempts >= MAX_SUBMISSIONS_PER_STAGE:
            raise HTTPException(status_code=429, detail="submission limit reached for this stage")

        submitted_flag = submission.flag.strip()
        accepted = hmac.compare_digest(submitted_flag, flag_for(stage["id"]))
        reason = "accepted" if accepted else "invalid flag"
        db.execute(
            "INSERT INTO submissions(learner_id, stage_id, flag_digest, accepted, reason, submitted_at) VALUES (?, ?, ?, ?, ?, ?)",
            (learner_id, stage["id"], digest_flag(submitted_flag), int(accepted), reason, utc_now()),
        )
        if accepted:
            db.execute(
                "INSERT OR IGNORE INTO completions(learner_id, stage_id, completed_at) VALUES (?, ?, ?)",
                (learner_id, stage["id"], utc_now()),
            )
        db.commit()
        if accepted:
            sync_active_artifact(learner_id, completed | {stage["id"]})
        if not accepted:
            raise HTTPException(status_code=401, detail="invalid hard flag")

        next_stage = next((candidate for candidate in CURRICULUM["stages"] if candidate["id"] not in completed and stage_status(candidate, completed | {stage["id"]}) == "unlocked"), None)
        return {
            "accepted": True,
            "stage_id": stage["id"],
            "status": "completed",
            "next_stage_id": next_stage["id"] if next_stage else None,
            "message": "hard flag accepted; next stage unlocked" if next_stage else "hard flag accepted; curriculum complete",
        }
    finally:
        db.close()
