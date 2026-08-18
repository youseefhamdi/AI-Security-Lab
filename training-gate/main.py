"""Zodiac Bank progression API.

This service is deliberately an adapter: catalog policy, learner state, and
flag derivation live in ``lab_core`` so the challenge surface cannot drift from
the gate. It exposes only redacted progress and never returns a plaintext flag
unless a challenge has already issued the corresponding hard-gate artifact.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

SERVICE_ROOT = Path(__file__).resolve().parent.parent
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from lab_core import Catalog, ProgressStore, RuntimeConfig, gate_flag, normalize_flag as core_normalize_flag, stage_flag, utc_now, validate_id, validate_security

CONFIG = RuntimeConfig.from_env()
CATALOG = Catalog.load(CONFIG)
validate_security(CONFIG)
STORE = ProgressStore(CONFIG, CATALOG)
FLAG_HEX_LENGTH = 32
MAX_SUBMISSIONS_PER_STAGE = CONFIG.max_submissions
FLAG_COOLDOWN_SECONDS = CONFIG.cooldown_seconds
ADMIN_KEY = CONFIG.admin_key
SECURITY_MODE = CONFIG.security_mode
APP_TITLE = "Zodiac Bank AI Security Training Gate"

app = FastAPI(title=APP_TITLE, version="4.0")
app.add_middleware(CORSMiddleware, allow_origin_regex=r"https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?", allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def security_headers(request: Any, call_next: Any) -> Any:
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'"
    return response


def flag_for(stage_id: str) -> str:
    return stage_flag(CONFIG.secret, stage_id)


def completed_stages(db: Any, learner_id: str) -> set[str]:
    return STORE.completed_stages(db, learner_id)


def promote_profile(db: Any, learner_id: str, completed: set[str]) -> dict[str, Any]:
    return STORE.promote(db, learner_id, completed)


def gate_flag_for(gate_id: str) -> str:
    return gate_flag(CONFIG.secret, gate_id)


def normalize_flag(value: Any) -> str:
    return core_normalize_flag(value)


def digest_flag(flag: str) -> str:
    return hashlib.sha256(flag.encode("utf-8")).hexdigest()


def validate_learner(value: Any) -> str:
    try:
        return validate_id(value, label="learner_id")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def require_access(db: Any, learner_id: str, token: str) -> None:
    try:
        STORE.require_access(db, learner_id, token)
    except PermissionError as exc:
        raise HTTPException(status_code=401 if not token else 403, detail=str(exc)) from exc


def ensure(db: Any, learner_id: str) -> None:
    STORE.ensure_learner(db, learner_id)


def stage_view(stage: dict[str, Any], completed: set[str], db: Any, learner_id: str) -> dict[str, Any]:
    status = STORE.stage_status(str(stage["id"]), completed)
    attempts = db.execute("SELECT COUNT(*) AS count FROM submissions WHERE learner_id=? AND stage_id=?", (learner_id, stage["id"])).fetchone()["count"]
    view = {key: value for key, value in stage.items() if key != "hints"}
    view.update({
        "status": status,
        "attempts": int(attempts),
        "flag_required": True,
        "flag_format": f"ZODIAC-BANK-{str(stage['id']).upper()}-<{FLAG_HEX_LENGTH} HEX CHARACTERS>",
        "hints": stage.get("hints", []) if status != "locked" else [],
        "hints_available": len(stage.get("hints", [])) if status != "locked" else 0,
    })
    return view


def current_stage(completed: set[str]) -> str | None:
    return STORE.current_stage(completed)


def failed_attempt_cooldown(db: Any, *, table: str, id_column: str, learner_id: str, item_id: str) -> int:
    if FLAG_COOLDOWN_SECONDS <= 0 or table not in {"submissions", "gate_submissions"} or id_column not in {"stage_id", "gate_id"}:
        return 0
    row = db.execute(f"SELECT submitted_at FROM {table} WHERE learner_id=? AND {id_column}=? AND accepted=0 ORDER BY submission_id DESC LIMIT 1", (learner_id, item_id)).fetchone()
    if row is None: return 0
    from datetime import datetime, timezone
    try: last = datetime.fromisoformat(str(row["submitted_at"]))
    except ValueError: return 0
    if last.tzinfo is None: last = last.replace(tzinfo=timezone.utc)
    return max(0, int(FLAG_COOLDOWN_SECONDS - (datetime.now(timezone.utc) - last).total_seconds()))


def flag_format_error(flag: str, *, expected_prefix: str) -> str | None:
    if not flag.startswith(expected_prefix): return f"flag format: expected prefix '{expected_prefix}'"
    body = flag[len(expected_prefix):]
    if not body or not re.fullmatch(r"[A-Z0-9-]{4,96}", body): return "flag format: expected an uppercase alphanumeric body after the prefix"
    return None


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


def require_admin(x_training_admin_key: str = Header(default="")) -> None:
    if not ADMIN_KEY or not hmac.compare_digest(x_training_admin_key, ADMIN_KEY):
        raise HTTPException(status_code=403, detail="instructor admin key required")


@app.get("/health")
def health() -> dict[str, Any]:
    db_ok = True
    try:
        db = STORE.connect(); db.execute("SELECT 1"); db.close()
    except Exception:  # noqa: BLE001
        db_ok = False
    return {"status": "healthy" if db_ok else "degraded", "service": "training-gate", "version": "4.0", "lab": CATALOG.curriculum["title"], "stages": len(CATALOG.stages), "scenarios": len(CATALOG.scenarios), "hard_gates": len(CATALOG.gates), "database": "ok" if db_ok else "unavailable", "flags": "hmac-backed-stage-and-gate"}


@app.get("/api/curriculum")
def curriculum(learner_id: str = "default", x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id = validate_learner(learner_id); db = STORE.connect()
    try:
        require_access(db, learner_id, x_training_learner_token); ensure(db, learner_id)
        completed = STORE.completed_stages(db, learner_id); STORE.sync_artifact(learner_id, completed)
        return {"lab_id": CATALOG.curriculum["lab_id"], "title": CATALOG.curriculum["title"], "learner_id": learner_id, "bank_profile": STORE.profile(db, learner_id), "stages": [stage_view(stage, completed, db, learner_id) for stage in CATALOG.curriculum["stages"]]}
    finally: db.close()


@app.get("/api/progress/{learner_id}")
def progress(learner_id: str, x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    return curriculum(learner_id, x_training_learner_token)


@app.get("/api/bank/profile")
def bank_profile(learner_id: str = "default", x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id = validate_learner(learner_id); db = STORE.connect()
    try:
        require_access(db, learner_id, x_training_learner_token); ensure(db, learner_id); return {"learner_id": learner_id, "profile": STORE.profile(db, learner_id)}
    finally: db.close()


def gate_view(gate: dict[str, Any], completed_stages: set[str], completed_gates: set[str]) -> dict[str, Any]:
    return {"gate_id": gate["gate_id"], "stage_id": gate["stage_id"], "rank": gate["rank"], "title": gate["title"], "scenario_ids": gate["scenario_ids"], "detection_rule_ids": gate["detection_rule_ids"], "required_controls": gate["required_controls"], "concepts": gate["concepts"], "status": STORE.gate_status(gate, completed_stages, completed_gates), "flag_required": True, "flag_format": f"ZODIAC-BANK-GATE-{str(gate['gate_id']).upper()}-<{FLAG_HEX_LENGTH} HEX CHARACTERS>"}


@app.get("/api/gates")
def gates(learner_id: str = "default", x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id = validate_learner(learner_id); db = STORE.connect()
    try:
        require_access(db, learner_id, x_training_learner_token); ensure(db, learner_id)
        completed_stages = STORE.completed_stages(db, learner_id); completed_gates = STORE.completed_gates(db, learner_id); stage = STORE.current_stage(completed_stages)
        visible = [gate_view(gate, completed_stages, completed_gates) for gate in CATALOG.gates_by_stage.get(stage, ())] if stage else []
        active = STORE.current_gate(completed_stages, completed_gates)
        return {"learner_id": learner_id, "stage_id": stage, "current_gate_id": active["gate_id"] if active else None, "completed_gate_count": len(completed_gates), "total_hard_gates": len(CATALOG.gates), "bank_profile": STORE.profile(db, learner_id), "gates": visible}
    finally: db.close()


@app.post("/api/gates/submit")
def submit_gate(submission: GateSubmission, x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id = validate_learner(submission.learner_id); gate = CATALOG.gates_by_id.get(submission.gate_id)
    if gate is None: raise HTTPException(status_code=404, detail="unknown hard gate")
    db = STORE.connect()
    try:
        require_access(db, learner_id, x_training_learner_token); ensure(db, learner_id)
        completed_stages = STORE.completed_stages(db, learner_id); completed_gates = STORE.completed_gates(db, learner_id)
        status = STORE.gate_status(gate, completed_stages, completed_gates)
        if status == "locked": raise HTTPException(status_code=403, detail="complete the previous hard gate first")
        if status == "completed": return {"accepted": True, "gate_id": gate["gate_id"], "status": "completed", "message": "hard gate already completed"}
        db.execute("BEGIN IMMEDIATE")
        completed_stages = STORE.completed_stages(db, learner_id); completed_gates = STORE.completed_gates(db, learner_id); status = STORE.gate_status(gate, completed_stages, completed_gates)
        if status == "completed": db.commit(); return {"accepted": True, "gate_id": gate["gate_id"], "status": "completed", "message": "hard gate already completed"}
        if status == "locked": db.rollback(); raise HTTPException(status_code=403, detail="complete the previous hard gate first")
        attempts = int(db.execute("SELECT COUNT(*) AS count FROM gate_submissions WHERE learner_id=? AND gate_id=?", (learner_id, gate["gate_id"])).fetchone()["count"])
        if attempts >= MAX_SUBMISSIONS_PER_STAGE: db.rollback(); raise HTTPException(status_code=429, detail="hard-gate submission limit reached")
        submitted = normalize_flag(submission.flag); error = flag_format_error(submitted, expected_prefix="ZODIAC-BANK-")
        if error: db.rollback(); raise HTTPException(status_code=422, detail=error)
        cooldown = failed_attempt_cooldown(db, table="gate_submissions", id_column="gate_id", learner_id=learner_id, item_id=gate["gate_id"])
        if cooldown: db.rollback(); raise HTTPException(status_code=429, detail=f"submission cooldown active; retry in {cooldown}s")
        accepted = hmac.compare_digest(submitted, gate_flag_for(gate["gate_id"]))
        cursor = db.execute("INSERT INTO gate_submissions(learner_id,gate_id,flag_digest,accepted,reason,submitted_at) VALUES(?,?,?,?,?,?)", (learner_id, gate["gate_id"], digest_flag(submitted), int(accepted), "accepted" if accepted else "invalid flag", utc_now()))
        submission_id = int(cursor.lastrowid)
        if not accepted:
            db.commit(); raise HTTPException(status_code=401, detail=f"invalid hard-gate flag ({MAX_SUBMISSIONS_PER_STAGE-attempts} attempts remaining)")
        promoted_gates = completed_gates | {gate["gate_id"]}; stage_gate_ids = {item["gate_id"] for item in CATALOG.gates_by_stage[gate["stage_id"]]}; stage_completed = stage_gate_ids.issubset(promoted_gates); promoted_stages = set(completed_stages)
        if stage_completed:
            promoted_stages.add(gate["stage_id"]); db.execute("INSERT OR IGNORE INTO completions VALUES(?,?,?)", (learner_id, gate["stage_id"], utc_now()))
        db.execute("INSERT OR IGNORE INTO gate_completions VALUES(?,?,?)", (learner_id, gate["gate_id"], utc_now()))
        promoted_profile = promote_profile(db, learner_id, promoted_stages) if stage_completed else STORE.profile(db, learner_id)
        db.commit(); STORE.sync_artifact(learner_id, promoted_stages)
        next_gate = STORE.current_gate(promoted_stages, promoted_gates); next_stage = STORE.current_stage(promoted_stages)
        return {"accepted": True, "gate_id": gate["gate_id"], "stage_id": gate["stage_id"], "status": "stage_completed" if stage_completed else "completed", "stage_completed": stage_completed, "submission_id": submission_id, "attempts_used": attempts+1, "attempts_remaining": max(0, MAX_SUBMISSIONS_PER_STAGE-attempts-1), "next_gate_id": next_gate["gate_id"] if next_gate else None, "next_stage_id": next_stage, "hard_gate_count": len(promoted_gates), "bank_profile": promoted_profile, "message": "final hard gate accepted; next stage unlocked" if stage_completed else "hard gate accepted; next gate unlocked"}
    finally: db.close()


@app.get("/api/lessons/{stage_id}")
def lesson(stage_id: str, learner_id: str = "default", x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id = validate_learner(learner_id); db = STORE.connect()
    try:
        if stage_id not in CATALOG.stages: raise HTTPException(status_code=404, detail="unknown stage")
        require_access(db, learner_id, x_training_learner_token); ensure(db, learner_id); completed = STORE.completed_stages(db, learner_id)
        if STORE.stage_status(stage_id, completed) == "locked": raise HTTPException(status_code=403, detail="complete prerequisite stages first")
        stage = next(stage for stage in CATALOG.curriculum["stages"] if stage["id"] == stage_id)
        return {"learner_id": learner_id, "bank_profile": STORE.profile(db, learner_id), "stage": stage_view(stage, completed, db, learner_id)}
    finally: db.close()


@app.post("/api/admin/cohorts")
def create_cohort(request: CohortRequest, _: None = Depends(require_admin)) -> dict[str, Any]:
    cohort_id = validate_learner(request.cohort_id); db = STORE.connect()
    try:
        if db.execute("SELECT 1 FROM cohorts WHERE cohort_id=?", (cohort_id,)).fetchone(): raise HTTPException(status_code=409, detail="cohort already exists")
        db.execute("INSERT INTO cohorts VALUES(?,?,?)", (cohort_id, str(request.display_name).strip(), utc_now())); db.commit(); return {"cohort_id": cohort_id, "display_name": str(request.display_name).strip(), "members": 0}
    finally: db.close()


@app.get("/api/admin/cohorts")
def list_cohorts(_: None = Depends(require_admin)) -> dict[str, Any]:
    db = STORE.connect()
    try: return {"cohorts": [dict(row) for row in db.execute("SELECT c.cohort_id,c.display_name,c.created_at,COUNT(cm.learner_id) AS members FROM cohorts c LEFT JOIN cohort_members cm ON cm.cohort_id=c.cohort_id GROUP BY c.cohort_id ORDER BY c.created_at") ]}
    finally: db.close()


@app.post("/api/admin/cohorts/{cohort_id}/members")
def add_cohort_member(cohort_id: str, request: CohortMemberRequest, _: None = Depends(require_admin)) -> dict[str, Any]:
    cohort_id = validate_learner(cohort_id); learner_id = validate_learner(request.learner_id); db = STORE.connect()
    try:
        if not db.execute("SELECT 1 FROM cohorts WHERE cohort_id=?", (cohort_id,)).fetchone(): raise HTTPException(status_code=404, detail="cohort not found")
        ensure(db, learner_id); token = STORE.issue_token(db, learner_id); db.execute("INSERT OR IGNORE INTO cohort_members VALUES(?,?,?)", (cohort_id, learner_id, utc_now())); db.commit()
        return {"cohort_id": cohort_id, "learner_id": learner_id, "status": "member", "learner_token": token, "token_note": "deliver privately; plaintext is not stored"}
    finally: db.close()


@app.get("/api/admin/cohorts/{cohort_id}/report")
def completion_report(cohort_id: str, _: None = Depends(require_admin)) -> dict[str, Any]:
    cohort_id = validate_learner(cohort_id); db = STORE.connect()
    try:
        cohort = db.execute("SELECT * FROM cohorts WHERE cohort_id=?", (cohort_id,)).fetchone()
        if cohort is None: raise HTTPException(status_code=404, detail="cohort not found")
        members=[]
        for member in db.execute("SELECT learner_id,joined_at FROM cohort_members WHERE cohort_id=? ORDER BY learner_id", (cohort_id,)):
            completed=STORE.completed_stages(db, member["learner_id"]); members.append({"learner_id":member["learner_id"],"joined_at":member["joined_at"],"completed_count":len(completed),"total_stages":len(CATALOG.stages),"curriculum_status":"complete" if len(completed)==len(CATALOG.stages) else "in_progress","stages":{stage:STORE.stage_status(stage,completed) for stage in CATALOG.stages}})
        return {"cohort":dict(cohort),"generated_at":utc_now(),"members":members}
    finally: db.close()


@app.post("/api/admin/cohorts/{cohort_id}/reset")
def reset_cohort(cohort_id: str, _: None = Depends(require_admin)) -> dict[str, Any]:
    cohort_id=validate_learner(cohort_id); db=STORE.connect()
    try:
        members=db.execute("SELECT learner_id FROM cohort_members WHERE cohort_id=?",(cohort_id,)).fetchall()
        for row in members:
            learner=row["learner_id"]; db.execute("DELETE FROM completions WHERE learner_id=?",(learner,)); db.execute("DELETE FROM submissions WHERE learner_id=?",(learner,)); db.execute("DELETE FROM gate_completions WHERE learner_id=?",(learner,)); db.execute("DELETE FROM gate_submissions WHERE learner_id=?",(learner,)); db.execute("UPDATE learner_profiles SET profile_id=?,promotion_count=0,updated_at=? WHERE learner_id=?",(CATALOG.profiles["profiles"][0]["profile_id"],utc_now(),learner)); STORE.sync_artifact(learner,set())
        db.commit(); return {"cohort_id":cohort_id,"reset_members":len(members),"status":"reset"}
    finally: db.close()


@app.post("/api/flags/submit")
def submit_flag(submission: FlagSubmission, x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id=validate_learner(submission.learner_id); stage_id=submission.stage_id
    if stage_id not in CATALOG.stages: raise HTTPException(status_code=404, detail="unknown stage")
    db=STORE.connect()
    try:
        require_access(db,learner_id,x_training_learner_token); ensure(db,learner_id); completed=STORE.completed_stages(db,learner_id); status=STORE.stage_status(stage_id,completed)
        if status=="locked": raise HTTPException(status_code=403,detail="complete prerequisite stages first")
        if status=="completed": return {"accepted":True,"stage_id":stage_id,"status":"completed","message":"stage already completed"}
        db.execute("BEGIN IMMEDIATE"); completed=STORE.completed_stages(db,learner_id); status=STORE.stage_status(stage_id,completed)
        # completed = completed_stages(db, learner_id)
        # completed = completed_stages(db, learner_id)
        if status=="completed": db.commit(); return {"accepted":True,"stage_id":stage_id,"status":"completed","message":"stage already completed"}
        if SECURITY_MODE=="strict" and not {g["gate_id"] for g in CATALOG.gates_by_stage[stage_id]}.issubset(STORE.completed_gates(db,learner_id)): db.rollback(); raise HTTPException(status_code=403,detail="complete all hard gates in the current stage first")
        attempts=int(db.execute("SELECT COUNT(*) AS count FROM submissions WHERE learner_id=? AND stage_id=?",(learner_id,stage_id)).fetchone()["count"])
        submitted=normalize_flag(submission.flag); error=flag_format_error(submitted,expected_prefix="ZODIAC-BANK-")
        if error: db.rollback(); raise HTTPException(status_code=422,detail=error)
        accepted=hmac.compare_digest(submitted,flag_for(stage_id)); cur=db.execute("INSERT INTO submissions(learner_id,stage_id,flag_digest,accepted,reason,submitted_at) VALUES(?,?,?,?,?,?)",(learner_id,stage_id,digest_flag(submitted),int(accepted),"accepted" if accepted else "invalid flag",utc_now())); sid=int(cur.lastrowid)
        if not accepted: db.commit(); raise HTTPException(status_code=401,detail=f"invalid flag ({MAX_SUBMISSIONS_PER_STAGE-attempts} attempts remaining)")
        db.execute("INSERT OR IGNORE INTO completions VALUES(?,?,?)",(learner_id,stage_id,utc_now())); db.commit(); completed.add(stage_id); profile=promote_profile(db,learner_id,completed); STORE.sync_artifact(learner_id,completed)
        next_stage=STORE.current_stage(completed); return {"accepted":True,"stage_id":stage_id,"status":"completed","submission_id":sid,"attempts_remaining":max(0,MAX_SUBMISSIONS_PER_STAGE-attempts-1),"next_stage_id":next_stage,"bank_profile":profile,"message":"stage accepted; next stage unlocked"}
    finally: db.close()
