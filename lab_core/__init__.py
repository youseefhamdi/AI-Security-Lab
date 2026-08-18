"""Shared runtime primitives for the Zodiac Bank training platform.

The web services are intentionally thin adapters. This module owns the stable
rules that must not drift between the gate and challenge surfaces: catalog
loading, learner identity, stage/gate status, local SQLite state, and bounded
input validation.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in os.sys.path:
    os.sys.path.insert(0, str(SCRIPTS))

from zodiac_bank_profiles import load_profiles, profile_by_id, profile_for_stage, public_profile  # noqa: E402
from zodiac_scenario_engine import load_scenario_pack, validate_scenarios, scenario_map  # noqa: E402

LEARNER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
COHORT_PATTERN = LEARNER_PATTERN
DEFAULT_FLAG_SECRET = "zodiac-bank-change-this-training-secret"
FLAG_HEX_LENGTH = 32


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hmac_flag(secret: bytes, prefix: str, value: str) -> str:
    body = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").upper()
    mac = hmac.new(secret, f"{prefix}:{value}".encode("utf-8"), hashlib.sha256).hexdigest()[:FLAG_HEX_LENGTH].upper()
    return f"ZODIAC-BANK-{body}-{mac}"


def stage_flag(secret: bytes, stage_id: str) -> str:
    return hmac_flag(secret, "stage", stage_id)


def gate_flag(secret: bytes, gate_id: str) -> str:
    return hmac_flag(secret, "hard-gate", gate_id)


def validate_id(value: Any, *, label: str = "learner_id") -> str:
    text = str(value or "").strip()
    if not LEARNER_PATTERN.fullmatch(text):
        raise ValueError(f"{label} must match {LEARNER_PATTERN.pattern}")
    return text


def normalize_flag(value: Any) -> str:
    return " ".join(str(value or "").split()).upper()


@dataclass(frozen=True)
class RuntimeConfig:
    curriculum_path: Path
    scenario_path: Path
    profile_path: Path
    progress_db: Path
    challenge_db: Path
    artifact_dir: Path
    secret: bytes
    security_mode: str
    admin_key: str
    max_submissions: int = 20
    cooldown_seconds: int = 0

    @classmethod
    def from_env(cls, *, challenge: bool = False) -> "RuntimeConfig":
        curriculum = Path(os.environ.get("TRAINING_CURRICULUM", "/app/config/curriculum.json"))
        scenarios = Path(os.environ.get("TRAINING_SCENARIOS", "/app/config/scenarios.json"))
        profiles = Path(os.environ.get("TRAINING_BANK_PROFILES", "/app/config/bank-profiles.json"))
        for current, fallback in ((curriculum, ROOT / "training-config/curriculum.json"), (scenarios, ROOT / "training-config/scenarios.json"), (profiles, ROOT / "training-config/bank-profiles.json")):
            if not current.is_file() and fallback.is_file():
                if current == curriculum: curriculum = fallback
                elif current == scenarios: scenarios = fallback
                else: profiles = fallback
        progress = Path(os.environ.get("TRAINING_STATE_DB", os.environ.get("TRAINING_ACCESS_DB", "/var/lib/training/progress.sqlite3")))
        challenge_db = Path(os.environ.get("TRAINING_CHALLENGE_STATE_DB", "/var/lib/training/challenges.sqlite3"))
        return cls(
            curriculum_path=curriculum,
            scenario_path=scenarios,
            profile_path=profiles,
            progress_db=progress,
            challenge_db=challenge_db,
            artifact_dir=Path(os.environ.get("TRAINING_ARTIFACT_DIR", "/var/lib/training/learners")),
            secret=os.environ.get("TRAINING_FLAG_SECRET", DEFAULT_FLAG_SECRET).encode("utf-8"),
            security_mode=os.environ.get("TRAINING_SECURITY_MODE", "development"),
            admin_key=os.environ.get("TRAINING_ADMIN_KEY", ""),
            max_submissions=int(os.environ.get("TRAINING_MAX_SUBMISSIONS", "20")),
            cooldown_seconds=int(os.environ.get("TRAINING_FLAG_COOLDOWN_SECONDS", "0")),
        )


@dataclass(frozen=True)
class Catalog:
    curriculum: dict[str, Any]
    pack: dict[str, Any]
    profiles: dict[str, Any]
    stages: tuple[str, ...]
    scenarios: dict[str, dict[str, Any]]
    gates: tuple[dict[str, Any], ...]
    gates_by_id: dict[str, dict[str, Any]]
    gates_by_stage: dict[str, tuple[dict[str, Any], ...]]

    @classmethod
    def load(cls, config: RuntimeConfig) -> "Catalog":
        curriculum = json.loads(config.curriculum_path.read_text(encoding="utf-8"))
        pack = load_scenario_pack(config.scenario_path)
        validate_scenarios(pack, curriculum)
        profiles = load_profiles(config.profile_path)
        stages = tuple(str(item["id"]) for item in curriculum["stages"])
        gates = tuple(pack.get("hard_gates", []))
        return cls(curriculum, pack, profiles, stages, scenario_map(pack), gates, {str(g["gate_id"]): g for g in gates}, {stage: tuple(g for g in gates if g["stage_id"] == stage) for stage in stages})

    def stage(self, stage_id: str) -> dict[str, Any]:
        for stage in self.curriculum.get("stages", []):
            if stage.get("id") == stage_id:
                return stage
        raise KeyError(stage_id)

    def scenario(self, scenario_id: str) -> dict[str, Any]:
        try:
            return self.scenarios[scenario_id]
        except KeyError as exc:
            raise KeyError(f"unknown scenario: {scenario_id}") from exc


class ProgressStore:
    """Atomic learner, cohort, flag, and profile state."""

    def __init__(self, config: RuntimeConfig, catalog: Catalog) -> None:
        self.config = config
        self.catalog = catalog

    def connect(self) -> sqlite3.Connection:
        self.config.progress_db.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.config.progress_db)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS learners(learner_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS learner_access(learner_id TEXT PRIMARY KEY, token_digest TEXT NOT NULL, issued_at TEXT NOT NULL, FOREIGN KEY(learner_id) REFERENCES learners(learner_id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS submissions(submission_id INTEGER PRIMARY KEY AUTOINCREMENT, learner_id TEXT NOT NULL, stage_id TEXT NOT NULL, flag_digest TEXT NOT NULL, accepted INTEGER NOT NULL, reason TEXT NOT NULL, submitted_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS completions(learner_id TEXT NOT NULL, stage_id TEXT NOT NULL, completed_at TEXT NOT NULL, PRIMARY KEY(learner_id,stage_id));
        CREATE TABLE IF NOT EXISTS cohorts(cohort_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS cohort_members(cohort_id TEXT NOT NULL, learner_id TEXT NOT NULL, joined_at TEXT NOT NULL, PRIMARY KEY(cohort_id,learner_id));
        CREATE TABLE IF NOT EXISTS learner_profiles(learner_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, promotion_count INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS gate_completions(learner_id TEXT NOT NULL, gate_id TEXT NOT NULL, completed_at TEXT NOT NULL, PRIMARY KEY(learner_id,gate_id));
        CREATE TABLE IF NOT EXISTS gate_submissions(submission_id INTEGER PRIMARY KEY AUTOINCREMENT, learner_id TEXT NOT NULL, gate_id TEXT NOT NULL, flag_digest TEXT NOT NULL, accepted INTEGER NOT NULL, reason TEXT NOT NULL, submitted_at TEXT NOT NULL);
        """)
        db.commit()
        return db

    def ensure_learner(self, db: sqlite3.Connection, learner_id: str) -> None:
        now = utc_now()
        db.execute("INSERT INTO learners VALUES(?,?,?) ON CONFLICT(learner_id) DO UPDATE SET updated_at=excluded.updated_at", (learner_id, now, now))
        initial = self.catalog.profiles["profiles"][0]["profile_id"]
        db.execute("INSERT OR IGNORE INTO learner_profiles VALUES(?,?,?,?)", (learner_id, initial, 0, now))
        db.commit()

    def issue_token(self, db: sqlite3.Connection, learner_id: str) -> str:
        token = secrets.token_urlsafe(32)
        db.execute("INSERT INTO learner_access VALUES(?,?,?) ON CONFLICT(learner_id) DO UPDATE SET token_digest=excluded.token_digest,issued_at=excluded.issued_at", (learner_id, digest(token), utc_now()))
        return token

    def require_access(self, db: sqlite3.Connection, learner_id: str, token: str) -> None:
        if self.config.security_mode != "strict": return
        row = db.execute("SELECT token_digest FROM learner_access WHERE learner_id=?", (learner_id,)).fetchone()
        if row is None or not hmac.compare_digest(str(row["token_digest"]), digest(token)):
            raise PermissionError("invalid learner token")

    def completed_stages(self, db: sqlite3.Connection, learner_id: str) -> set[str]:
        return {str(row["stage_id"]) for row in db.execute("SELECT stage_id FROM completions WHERE learner_id=?", (learner_id,))}

    def completed_gates(self, db: sqlite3.Connection, learner_id: str) -> set[str]:
        return {str(row["gate_id"]) for row in db.execute("SELECT gate_id FROM gate_completions WHERE learner_id=?", (learner_id,))}

    def current_stage(self, completed: set[str]) -> str | None:
        return next((stage for stage in self.catalog.stages if stage not in completed), None)

    def current_gate(self, completed_stages: set[str], completed_gates: set[str]) -> dict[str, Any] | None:
        stage = self.current_stage(completed_stages)
        if stage is None: return None
        return next((gate for gate in self.catalog.gates_by_stage.get(stage, ()) if gate["gate_id"] not in completed_gates), None)

    def profile(self, db: sqlite3.Connection, learner_id: str) -> dict[str, Any]:
        row = db.execute("SELECT profile_id,promotion_count,updated_at FROM learner_profiles WHERE learner_id=?", (learner_id,)).fetchone()
        if row is None: raise RuntimeError("learner profile missing")
        profile = profile_by_id(self.catalog.profiles, str(row["profile_id"]))
        return public_profile(profile, promotion_count=int(row["promotion_count"]), updated_at=str(row["updated_at"]))

    def promote(self, db: sqlite3.Connection, learner_id: str, completed: set[str]) -> dict[str, Any]:
        profile = profile_for_stage(self.catalog.profiles, self.current_stage(completed))
        now = utc_now()
        db.execute("UPDATE learner_profiles SET profile_id=?,promotion_count=promotion_count+1,updated_at=? WHERE learner_id=?", (profile["profile_id"], now, learner_id))
        return self.profile(db, learner_id)

    def stage_status(self, stage_id: str, completed: set[str]) -> str:
        if stage_id in completed: return "completed"
        return "unlocked" if self.current_stage(completed) == stage_id else "locked"

    def gate_status(self, gate: dict[str, Any], completed_stages: set[str], completed_gates: set[str]) -> str:
        if gate["gate_id"] in completed_gates: return "completed"
        active = self.current_gate(completed_stages, completed_gates)
        return "unlocked" if active and active["gate_id"] == gate["gate_id"] else "locked"

    def sync_artifact(self, learner_id: str, completed: set[str]) -> None:
        target = self.config.artifact_dir / learner_id
        target.mkdir(parents=True, exist_ok=True)
        path = target / "active-challenge.json"
        stage = self.current_stage(completed)
        if stage is None: path.unlink(missing_ok=True); return
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"learner_id": learner_id, "stage_id": stage, "challenge_path": f"/stage/{stage}", "issued_at": utc_now()}, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)


class ChallengeStore:
    """Per-learner scenario state with chained, answer-free evidence."""

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config

    def connect(self) -> sqlite3.Connection:
        self.config.challenge_db.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.config.challenge_db)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS scenario_runs(learner_id TEXT NOT NULL,scenario_id TEXT NOT NULL,stage_id TEXT NOT NULL,step_index INTEGER NOT NULL DEFAULT 0,evidence_json TEXT NOT NULL DEFAULT '[]',status TEXT NOT NULL DEFAULT 'active',completion_token TEXT,nonce TEXT NOT NULL DEFAULT '',attempts INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(learner_id,scenario_id));
        CREATE TABLE IF NOT EXISTS scenario_events(learner_id TEXT NOT NULL,scenario_id TEXT NOT NULL,step_index INTEGER NOT NULL,event TEXT NOT NULL,evidence_digest TEXT NOT NULL,recorded_at TEXT NOT NULL,PRIMARY KEY(learner_id,scenario_id,step_index));
        """)
        db.commit(); return db


def validate_security(config: RuntimeConfig, *, require_admin: bool = True) -> None:
    if config.security_mode != "strict": return
    if config.secret == DEFAULT_FLAG_SECRET.encode() or len(config.secret) < 32:
        raise RuntimeError("strict security requires TRAINING_FLAG_SECRET with at least 32 bytes")
    if require_admin and (not config.admin_key or len(config.admin_key) < 24):
        raise RuntimeError("strict security requires TRAINING_ADMIN_KEY with at least 24 characters")


__all__ = ["Catalog", "ChallengeStore", "DEFAULT_FLAG_SECRET", "FLAG_HEX_LENGTH", "ProgressStore", "RuntimeConfig", "gate_flag", "hmac_flag", "normalize_flag", "stage_flag", "utc_now", "validate_id", "validate_security"]
