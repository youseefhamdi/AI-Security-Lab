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

from fastapi import FastAPI, Header, HTTPException, Request, Response
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
STAGE_COVER_ASSETS = {
    "L00-foundation": "stage-l00-foundation.png",
    "L01-recon": "stage-l01-recon.png",
    "L02-prompt-injection": "stage-l02-prompt-injection.png",
    "L03-rag": "stage-l03-rag.png",
    "L04-agent-protocols": "stage-l04-agent-protocols.png",
    "L05-memory": "stage-l05-memory.png",
    "L06-identity-control-plane": "stage-l06-identity.png",
    "L07-supply-chain": "stage-l07-supply-chain.png",
    "L08-detection-evasion": "stage-l08-detection-evasion.png",
    "L09-apt-capstone": "stage-l09-apt-capstone.png",
}


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

ASSETS_DIR = Path(__file__).resolve().parent / "docs" / "assets"
if not ASSETS_DIR.is_dir():
    # The trainer UI references ../docs/assets/... relative to the page URL,
    # which the browser resolves to /docs/assets/... The assets live in the
    # repo docs/ folder; mount them read-only when present so the cinematic
    # banner and spartan emblems render without being copied into the image.
    repo_assets = Path(__file__).resolve().parent.parent / "docs" / "assets"
    if repo_assets.is_dir():
        ASSETS_DIR = repo_assets
if ASSETS_DIR.is_dir():
    try:
        from fastapi.staticfiles import StaticFiles

        app.mount("/docs/assets", StaticFiles(directory=str(ASSETS_DIR)), name="docs-assets")
    except ImportError:
        # The offline evaluation harness stubs FastAPI without staticfiles;
        # the mount only matters when the real service runs in the container.
        pass


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


def flow_steps_view(scenario: dict[str, Any]) -> list[dict[str, Any]]:
    """Expose safe attack-flow metadata without expected evidence values."""
    return [
        {
            "step": index + 1,
            "id": str(step.get("id", f"s{index + 1}")),
            "event": str(step.get("event", "")),
            "observation": str(step.get("observation", "")),
            "evidence_keys": sorted(str(key) for key in step.get("evidence", {}).keys()),
        }
        for index, step in enumerate(scenario.get("steps", []))
    ]


BRANCH_COLORS = {
    "attack": "#ff3366",
    "defense": "#00ff88",
    "forensics": "#fbbf24",
    "recovery": "#00d4ff",
    "other": "#7c3aed",
}


def solution_playbook(scenario: dict[str, Any]) -> dict[str, str]:
    """Create scenario-aware, answer-safe operator guidance for every lab."""
    title = str(scenario.get("title", "the scenario"))
    objective = str(scenario.get("objective", "Preserve bounded evidence and explain the control decision."))
    tags = {str(tag).lower() for tag in scenario.get("threat_tags", [])}
    controls = ", ".join(str(control).replace("-", " ") for control in scenario.get("required_controls", [])) or "scope and evidence custody"
    detections = ", ".join(str(rule) for rule in scenario.get("detection_rule_ids", [])) or "the declared detection rules"
    playbook = {
        "mission": objective,
        "setup": "Start the lab, open only the disclosed localhost target, and capture the clean state before changing one variable.",
        "investigation": f"Trace {title} through its declared signal path. Compare what the surface claims with what the synthetic telemetry or response actually proves.",
        "decision": f"Classify the finding against {detections}; choose a bounded decision that enforces {controls}.",
        "finish": "Reconcile the observation, control decision, and evidence token before moving to the next question or hard gate.",
        "boundary": "Use synthetic data only. Do not use real credentials, external targets, production mailboxes, or irreversible actions.",
    }
    if "certificate" in tags or "pfx" in tags:
        playbook.update({"investigation": "In the synthetic telemetry, follow certificate discovery → safe copy evidence → password-recovery signal → certificate-authentication evidence. Record trace/span context without handling real keys.", "decision": f"Confirm the certificate path is contained and map it to {controls}; never execute certificate authentication outside the fixture."})
    elif "exchange" in tags or "mail" in tags or "journaling" in tags:
        playbook.update({"investigation": "Correlate the AI request with the synthetic PowerShell/Exchange or journaling event, then verify the mail-flow impact and identity that authorized it.", "decision": f"Stop unauthorized collection or transport manipulation and preserve the timeline under {controls}."})
    elif "resume" in tags or "document" in tags or "ocr" in tags:
        playbook.update({"investigation": "Inspect the document/image parsing boundary, distinguish visible content from instruction-like content, and compare the classifier decision with the review control.", "decision": f"Treat parsed content as untrusted data; require human review and {controls} before any decision is accepted."})
    elif "kerberos" in tags or "dcsync" in tags or "shadow-credentials" in tags or "shadow" in tags:
        playbook.update({"investigation": "Build a timeline from synthetic identity, directory, ticket, and authentication telemetry. Correlate the sequence without exposing hashes or requesting real tickets.", "decision": f"Rotate or quarantine the synthetic identity path and verify {controls} before closing the incident."})
    elif "rbcd" in tags or "delegation" in tags:
        playbook.update({"investigation": "Follow target discovery → machine-account creation → delegation configuration → privileged-access evidence in the synthetic timeline. Treat each transition as a separate authorization boundary.", "decision": f"Contain the delegation relationship, rotate the synthetic identity, and verify {controls}."})
    elif "prompt-injection" in tags or "injection" in tags:
        playbook.update({"investigation": "Compare the clean assistant response with the controlled untrusted fixture. Identify where content tries to become an instruction, tool call, or approval.", "decision": f"Keep the content untrusted, validate the output schema, and require {controls} before any consequential path."})
    elif "rag" in tags or "retrieval" in tags or "cache" in tags:
        playbook.update({"investigation": "Trace query, tenant, retrieval source, cache state, citation, and final response as separate records. Relevance is not authorization.", "decision": f"Quarantine the questionable source or cache entry and enforce {controls}."})
    elif "supply-chain" in tags or "dependency" in tags or "package" in tags or "model" in tags:
        playbook.update({"investigation": "Follow the synthetic artifact from publisher or registry through digest, manifest, workspace, and runtime load. Treat names and version claims as untrusted.", "decision": f"Hold promotion until provenance, digest, workspace scope, and {controls} are independently verified."})
    return playbook


def _curl_command(path: str, method: str = "GET", *, query: str = "", headers: list[str] | None = None, body: dict[str, Any] | None = None) -> str:
    """Build a copy/paste-safe command against the local challenge service."""
    command = ["curl", "-sS", "-i", "-X", method, '"$LAB' + path + (query or "") + '"']
    for header in ["X-Training-Learner-Token: $TOKEN", *(headers or [])]:
        command.extend(["-H", json.dumps(header)])
    if body is not None:
        command.extend(["-H", json.dumps("Content-Type: application/json"), "--data", json.dumps(json.dumps(body, separators=(",", ":")))])
    return " ".join(command)


def _technical_family(scenario: dict[str, Any]) -> str:
    scenario_id = str(scenario.get("id", "")).lower()
    haystack = " ".join([scenario_id, str(scenario.get("title", "")), *[str(tag) for tag in scenario.get("threat_tags", [])]]).lower()
    if "certificate" in haystack or "pfx" in haystack:
        return "certificate"
    if "mail" in haystack or "exchange" in haystack or "journaling" in haystack:
        return "mail"
    if "resume" in haystack or "ocr" in haystack or "document" in haystack:
        return "document"
    if "rbcd" in haystack or "delegation" in haystack:
        return "rbcd"
    if "kerberos" in haystack or "dcsync" in haystack or "shadow" in haystack:
        return "identity-telemetry"
    if "vault" in haystack or "llm" in haystack or "prompt" in haystack or "injection" in haystack:
        return "prompt"
    return str(scenario.get("stage_id", "generic"))


def technical_runbook(scenario: dict[str, Any]) -> dict[str, Any]:
    """Return an executable, answer-safe local runbook for a scenario.

    Commands deliberately point at the challenge service and synthetic fixtures.
    They teach the operator how to produce observations; HMAC-derived evidence
    values remain learner/run-specific and are never placed in the guide.
    """
    scenario_id = str(scenario["id"])
    family = _technical_family(scenario)
    steps = list(scenario.get("steps", []))
    base = f"/api/scenarios/{scenario_id}/telemetry?learner_id=$LEARNER"
    procedures: list[dict[str, Any]] = []
    if family == "certificate":
        queries = [
            ('observer.type:"apm-server" AND service.framework.name:"langgraph-planner"', "Find the planning span and record its trace/span identifiers."),
            ('labels.user_input:search_certificate_files AND event.outcome:success', "Confirm certificate discovery completed and count only synthetic artifacts."),
            ('span.name:tool_crack_pfx_files AND event.outcome:success', "Confirm the password-recovery signal; record the wordlist path, never a password."),
        ]
        commands = [_curl_command(base, query="&query=" + query.replace(" ", "%20")) for query, _ in queries]
        expected = ["plan_llm / langgraph-planner fields", "search_certificate_files with success", "crack_pfx_files with a redacted result preview"]
        remediation = "Verify the fixture shows certificate discovery, copy, cracking, and authentication as separate authorized events; quarantine the synthetic certificate path and rotate the synthetic identity."
    elif family == "mail":
        queries = [
            ('service.name:"redteam_crew" AND labels.user_request:DCSync', "Locate the AI request and its selected credential-access plan."),
            ('labels.final_output:TransportRule', "Correlate AI output with Exchange rule creation."),
            ('powershell.file.script_block_text:DeleteMessage', "Verify the destructive mail-flow action in PowerShell telemetry."),
        ]
        commands = [_curl_command(base, query="&query=" + query.replace(" ", "%20")) for query, _ in queries]
        expected = ["DCSync / DRSUAPI request", "New-TransportRule", "DeleteMessage with pre-delivery suppression"]
        remediation = "Disable the synthetic transport rule, revoke the delegated Exchange identity, preserve message-tracking evidence, and require re-approval for mail-flow changes."
    elif family == "document":
        queries = [
            ('pipeline:document-parser AND source:resume-fixture', "Establish the visible-document baseline and parser provenance."),
            ('parser_output:instruction-like AND occlusion:hidden', "Identify hidden or low-contrast content treated as an instruction."),
            ('decision:shortlist AND review:missing', "Prove the classifier decision crossed the human-review boundary."),
        ]
        commands = [_curl_command(base, query="&query=" + query.replace(" ", "%20")) for query, _ in queries]
        expected = ["document parser source and checksum", "instruction-like parsed content", "shortlist decision without review approval"]
        remediation = "Quarantine the document fixture, discard parser instructions from the decision context, and require an independent human review record before accepting a hiring decision."
    elif family == "rbcd":
        queries = [
            ('tool:find_rbcd AND event.outcome:success', "Identify the synthetic computer with delegation configured."),
            ('tool:create_machine_account AND event.outcome:success', "Record the created machine account identifier without reusing its secret."),
            ('tool:set_rbcd OR tool:exploit_rbcd', "Correlate delegation configuration with the final administrative-share access signal."),
        ]
        commands = [_curl_command(base, query="&query=" + query.replace(" ", "%20")) for query, _ in queries]
        expected = ["find_rbcd result", "create_machine_account result with secret redacted", "set_rbcd followed by exploit_rbcd and C$ access"]
        remediation = "Remove the synthetic delegation relationship, delete the synthetic machine account, rotate its secret, and verify the target's delegation allow-list is restored."
    elif family == "identity-telemetry":
        queries = [
            ('identity:directory AND operation:DCSync', "Locate directory replication or credential-access telemetry."),
            ('authentication:kerberos OR operation:ticket', "Correlate ticket generation with the requesting synthetic identity."),
            ('privilege:administrator AND outcome:success', "Validate the privileged authentication signal without exposing hashes or tickets."),
        ]
        commands = [_curl_command(base, query="&query=" + query.replace(" ", "%20")) for query, _ in queries]
        expected = ["DCSync or directory replication event", "ticket operation with audience/identity", "privileged authentication success with secrets redacted"]
        remediation = "Disable and rotate the synthetic identity, invalidate its tickets, review directory replication rights, and verify the least-privilege policy."
    elif family == "prompt":
        queries = [
            ('surface:assistant AND view:baseline', "Capture a normal assistant response before introducing untrusted content."),
            ('content:instruction-like AND trust:untrusted', "Locate the prompt, email, OCR, or tool-description content that crossed the instruction boundary."),
            ('action:tool-call OR action:approval', "Verify whether validation and approval stopped the consequential path."),
        ]
        commands = [_curl_command(base, query="&query=" + query.replace(" ", "%20")) for query, _ in queries]
        expected = ["baseline assistant/tool inventory", "untrusted instruction-like content", "blocked or approval-bound action"]
        remediation = "Keep retrieved/document content in a data-only context, validate structured output, allow-list tools, and require explicit approval for every consequential action."
    else:
        endpoint_map = {
            "L00-foundation": ("/health", "Confirm the service is healthy and local before touching the scenario."),
            "L01-recon": ("/api/models?debug=1", "Compare normal and diagnostic model inventory responses and headers."),
            "L02-prompt-injection": ("/api/support/chat", "Send a harmless controlled message and compare it with a baseline response."),
            "L03-rag": ("/api/rag/query", "Compare published-only retrieval with the explicit synthetic draft fixture."),
            "L04-agent-protocols": ("/api/agent/dispatch", "Test the delegated tool/path boundary using the documented synthetic request."),
            "L05-memory": ("/api/memory/search", "Compare one synthetic user/run scope with a different run scope."),
            "L06-identity-control-plane": ("/api/admin/approval", "Compare a request without identity claims with the synthetic risk-engine claim."),
            "L07-supply-chain": ("/api/ci/artifacts", "Test artifact-name validation with a traversal-shaped synthetic name."),
            "L08-detection-evasion": ("/api/logs/search", "Compare normal search normalization with the documented stealth fixture."),
            "L09-apt-capstone": ("/api/campaign/execute", "Submit only previously earned synthetic evidence and inspect campaign correlation."),
        }
        endpoint, purpose = endpoint_map.get(str(scenario.get("stage_id")), ("/health", "Confirm the local challenge service is available."))
        commands = [_curl_command(endpoint)]
        queries = [(endpoint, purpose)]
        expected = ["HTTP status, response body, and relevant response headers"]
        remediation = "Record the control failure, apply the scenario's required controls, and rerun the same local request to verify the decision changed."
    for index, step in enumerate(steps):
        query, purpose = queries[min(index, len(queries) - 1)]
        evidence_keys = sorted(str(key) for key in step.get("evidence", {}).keys())
        procedures.append({
            "step": index + 1,
            "event": str(step.get("event", f"step-{index + 1}")),
            "operation": purpose,
            "request": commands[min(index, len(commands) - 1)],
            "expected_observation": expected[min(index, len(expected) - 1)],
            "record": ["timestamp", "HTTP status", "response fields", *evidence_keys],
            "evidence_keys": evidence_keys,
            "query": query,
        })
    return {
        "family": family,
        "target": "http://127.0.0.1:8060",
        "prerequisites": ["Start the lab and use the disclosed localhost target only.", "Set LAB=http://127.0.0.1:8060 and keep the learner token out of screenshots.", "Capture headers and body separately; do not paste secrets into evidence."],
        "start_command": f'LAB=http://127.0.0.1:8060; SCENARIO={scenario_id}; curl -sS -X POST "$LAB/api/scenarios/$SCENARIO/start" -H "X-Training-Learner-Token: $TOKEN" -H "Content-Type: application/json" --data \'{{"learner_id":"$LEARNER"}}\'',
        "procedures": procedures,
        "remediation": remediation,
        "cleanup": f'curl -sS -X POST "$LAB/api/scenarios/{scenario_id}/reset" -H "X-Training-Learner-Token: $TOKEN" -H "Content-Type: application/json" --data \'{{"learner_id":"$LEARNER"}}\'',
        "evidence_note": "The guide exposes evidence keys and safe observations, not the per-run expected values. Use Next-step hint and the candidate chips, then submit the event in order.",
    }


def solution_guide_view(scenario: dict[str, Any]) -> dict[str, Any]:
    """Build a technical, answer-safe walkthrough tied to local procedures."""
    branch = str(scenario.get("branch", "forensics"))
    playbook = solution_playbook(scenario)
    runbook = technical_runbook(scenario)
    method_by_branch = {
        "attack": "Reproduce the declared signal on the localhost target, preserve the request/response pair, then stop before any real side effect.",
        "defense": "Establish the clean baseline first, introduce one controlled variation, compare the response, and rerun after remediation.",
        "forensics": "Preserve headers and body, validate provenance, correlate the event with the declared detection rule, and document the decision.",
        "recovery": "Contain the synthetic impact, rotate or quarantine the affected identity/data path, then rerun the same verification request.",
    }
    phases = ["Orient", "Observe", "Validate", "Contain", "Recover"]
    steps = []
    total = len(scenario.get("steps", []))
    for index, step in enumerate(scenario.get("steps", [])):
        event = str(step.get("event", f"step-{index + 1}"))
        title = " ".join(word.capitalize() for word in re.split(r"[_-]+", event) if word)
        evidence_keys = sorted(str(key) for key in step.get("evidence", {}).keys())
        procedure = runbook["procedures"][min(index, len(runbook["procedures"]) - 1)]
        phase = phases[min(index, len(phases) - 1)]
        steps.append({
            "step": index + 1,
            "event": event,
            "title": title or f"Step {index + 1}",
            "phase": phase,
            "action": procedure["operation"],
            "look_for": procedure["expected_observation"],
            "text": str(step.get("observation", "")),
            "method": method_by_branch.get(branch, method_by_branch["forensics"]),
            "request": procedure["request"],
            "query": procedure["query"],
            "record": procedure["record"],
            "success_look": f"The request produces the expected observation; record {', '.join(evidence_keys) or 'bounded evidence'} and submit the machine event in order.",
            "evidence_keys": evidence_keys,
            "figure": f"/assets/solution/{scenario['id']}/{index + 1}.svg",
        })
    return {
        "version": "technical-runbook-v3",
        "intro": "Technical runbook: execute the local request, inspect the response/telemetry, record the declared fields, and submit the matching evidence event. The guide does not disclose the per-run answers.",
        "reel": f"/assets/solution/{scenario['id']}/reel.svg",
        "motion": {"duration_seconds": 8, "format": "animated-svg", "reduced_motion_supported": True},
        "method": "Setup → baseline → controlled test → correlate → remediate → verify. Every chapter maps to one machine-checked event.",
        "playbook": playbook,
        "runbook": runbook,
        "steps": steps,
    }


def _wrap_svg_text(text: str, max_chars: int = 60) -> list[str]:
    words = str(text).split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines[:4] or [""]


def solution_figure_svg(scenario: dict[str, Any], step_number: int) -> str:
    """Render a step diagram in the lab's visual language (dark surface, accent per branch)."""
    steps = scenario.get("steps", [])
    if step_number < 1 or step_number > len(steps):
        raise HTTPException(status_code=404, detail="solution figure not found")
    step = steps[step_number - 1]
    event = str(step.get("event", f"step {step_number}"))
    observation = str(step.get("observation", ""))
    evidence_keys = sorted(str(key) for key in step.get("evidence", {}).keys())
    branch = str(scenario.get("branch", "other"))
    accent = BRANCH_COLORS.get(branch, BRANCH_COLORS["other"])
    stage_id = str(scenario.get("stage_id", ""))
    scenario_id = str(scenario.get("id", ""))
    width, height = 840, 400
    runbook = technical_runbook(scenario)
    procedure = runbook["procedures"][step_number - 1] if step_number <= len(runbook["procedures"]) else None
    figure_copy = f"{procedure['operation']}. Observe: {procedure['expected_observation']}." if procedure else observation
    lines = _wrap_svg_text(figure_copy)
    esc_text = lambda value: (  # noqa: E731 - tiny local escaper for SVG text
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )
    chip_y = 196
    chip_x = 44
    chip_count = len(evidence_keys)
    chip_rows = max(1, (chip_count + 3) // 4)
    chips = []
    for index, key in enumerate(evidence_keys):
        column = index % 4
        row = index // 4
        cx = chip_x + column * 190
        cy = chip_y + row * 38
        label = key.upper()
        chips.append(
            f'<g><rect x="{cx}" y="{cy}" width="178" height="28" rx="8" fill="#12333d" stroke="{accent}" stroke-opacity=".55" stroke-width="1.2"/>'
            f'<text x="{cx + 89}" y="{cy + 19}" text-anchor="middle" fill="#7df3ff" font-family="monospace" font-size="13" font-weight="700" letter-spacing="1">{esc_text(label)}</text></g>'
        )
    text_y = 96
    body_lines = "".join(
        f'<text x="44" y="{text_y + index * 26}" fill="#d7d7e2" font-family="Arial, sans-serif" font-size="17">{esc_text(line)}</text>'
        for index, line in enumerate(lines)
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">
<defs>
  <style>
    @keyframes solutionScan {{ from {{ transform: translateX(-120px); }} to {{ transform: translateX(920px); }} }}
    @keyframes solutionPulse {{ 0%, 100% {{ opacity: .45; }} 50% {{ opacity: 1; }} }}
    @keyframes solutionDraw {{ from {{ stroke-dashoffset: 700; }} to {{ stroke-dashoffset: 0; }} }}
    .solution-scan {{ animation: solutionScan 4.5s linear infinite; opacity: .1; }}
    .solution-pulse {{ animation: solutionPulse 2.2s ease-in-out infinite; }}
    .solution-draw {{ stroke-dasharray: 700; animation: solutionDraw 3.5s ease-out infinite alternate; }}
    @media (prefers-reduced-motion: reduce) {{ .solution-scan, .solution-pulse, .solution-draw {{ animation: none; }} }}
  </style>
  <radialGradient id="g" cx="0.82" cy="0.06" r="0.9">
    <stop offset="0" stop-color="{accent}" stop-opacity=".28"/>
    <stop offset="1" stop-color="#0a0a0f" stop-opacity="0"/>
  </radialGradient>
  <linearGradient id="bar" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0" stop-color="{accent}"/>
    <stop offset="1" stop-color="#7c3aed"/>
  </linearGradient>
</defs>
<rect width="{width}" height="{height}" fill="#0a0a0f"/>
<rect width="{width}" height="{height}" fill="url(#g)"/>
<path class="solution-scan" d="M0 0V400" stroke="{accent}" stroke-width="150"/>
<path class="solution-draw" d="M44 178 C220 120 360 240 520 170 S720 120 796 178" fill="none" stroke="{accent}" stroke-opacity=".5" stroke-width="2"/>
<rect y="0" width="{width}" height="4" fill="url(#bar)" opacity=".85"/>
<g stroke="#1d1d28" stroke-width="1">
  <path d="M0 94h{width}"/><path d="M0 176h{width}"/><path d="M0 258h{width}"/><path d="M0 344h{width}"/>
  <path d="M210 0v{height}"/><path d="M420 0v{height}"/><path d="M630 0v{height}"/>
</g>
<rect x="40" y="36" width="58" height="30" rx="9" fill="{accent}" opacity=".16"/>
<text x="69" y="56" text-anchor="middle" fill="{accent}" font-family="monospace" font-size="15" font-weight="700" letter-spacing="1.5">STEP {step_number}</text>
<text x="112" y="57" fill="#ffffff" font-family="monospace" font-size="21" font-weight="700" letter-spacing="1.2">{esc_text(event.upper())}</text>
<g class="solution-pulse" transform="translate(666, 52) scale(1.15)">
  <circle cx="0" cy="0" r="11" fill="none" stroke="{accent}" stroke-width="2.6"/>
  <path d="M0 -5 L0 2 M0 5.5 L0 6.5" stroke="{accent}" stroke-width="2.2" stroke-linecap="round"/>
</g>
{body_lines}
<text x="44" y="{chip_y + chip_rows * 38 + 30}" fill="#8f8fa3" font-family="monospace" font-size="11.5" letter-spacing="1.6">EVIDENCE KEYS REQUIRED</text>
{''.join(chips)}
<text x="44" y="{height - 22}" fill="#5c5c70" font-family="monospace" font-size="11.5" letter-spacing="1" >{esc_text(scenario_id)} · {esc_text(stage_id)} · {esc_text(branch)} lane</text>
<text x="{width - 44}" y="{height - 22}" text-anchor="end" fill="#5c5c70" font-family="monospace" font-size="11.5" letter-spacing="1">ZODIAC BANK · AI SECURITY RANGE</text>
</svg>"""


def solution_reel_svg(scenario: dict[str, Any]) -> str:
    """Render an answer-safe animated storyboard for the solution guide."""
    steps = list(scenario.get("steps", []))
    if not steps:
        raise HTTPException(status_code=404, detail="solution reel not found")
    branch = str(scenario.get("branch", "other"))
    accent = BRANCH_COLORS.get(branch, BRANCH_COLORS["other"])
    scenario_id = str(scenario.get("id", ""))
    esc_text = lambda value: (  # noqa: E731 - tiny local escaper for SVG text
        str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )
    width, height = 960, 420
    count = len(steps)
    points = []
    labels = []
    for index, step in enumerate(steps):
        x = 90 + (790 * index / max(count - 1, 1))
        points.append(f"{x:.1f},220")
        label = " ".join(word.capitalize() for word in re.split(r"[_-]+", str(step.get("event", "step"))) if word)
        labels.append(
            f'<g class="reel-node" style="--delay:{index * 1.2:.1f}s"><circle cx="{x:.1f}" cy="220" r="22"/><text x="{x:.1f}" y="226" text-anchor="middle">{index + 1:02d}</text><text class="reel-label" x="{x:.1f}" y="278" text-anchor="middle">{esc_text(label[:22])}</text></g>'
        )
    polyline = " ".join(points)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">
<title id="title">Animated solution method for {esc_text(scenario_id)}</title>
<desc id="desc">A looping evidence path moving through {count} ordered solution steps.</desc>
<style>
  :root {{ color-scheme: dark; }}
  @keyframes reelPulse {{ 0%, 100% {{ opacity: .55; transform: scale(1); }} 50% {{ opacity: 1; transform: scale(1.18); }} }}
  @keyframes reelTravel {{ from {{ stroke-dashoffset: 900; }} to {{ stroke-dashoffset: 0; }} }}
  @keyframes reelScan {{ from {{ transform: translateX(-80px); }} to {{ transform: translateX(1040px); }} }}
  .reel-node {{ transform-box: fill-box; transform-origin: center; animation: reelPulse 2.4s ease-in-out var(--delay) infinite; }}
  .reel-path {{ fill: none; stroke: {accent}; stroke-width: 4; stroke-linecap: round; stroke-linejoin: round; stroke-dasharray: 8 14; animation: reelTravel 8s linear infinite; }}
  .reel-scan {{ opacity: .12; animation: reelScan 4.8s linear infinite; }}
  text {{ font-family: 'Fira Code', monospace; fill: #f8fafc; }}
  .reel-label {{ font-size: 13px; fill: #a8b2c7; }}
  @media (prefers-reduced-motion: reduce) {{ .reel-node, .reel-path, .reel-scan {{ animation: none; }} }}
</style>
<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#08121c"/><stop offset=".55" stop-color="#111827"/><stop offset="1" stop-color="#1b1034"/></linearGradient><filter id="glow"><feGaussianBlur stdDeviation="7" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>
<rect width="{width}" height="{height}" rx="24" fill="url(#bg)"/><path d="M0 74H{width}M0 344H{width}" stroke="#ffffff" stroke-opacity=".08"/><path class="reel-scan" d="M0 0V420" stroke="{accent}" stroke-width="120" filter="url(#glow)"/>
<text x="42" y="48" font-size="12" letter-spacing="2.5" fill="{accent}">MOTION STUDY · EVIDENCE METHOD</text><text x="42" y="82" font-size="24" font-weight="700">Follow the signal. Prove the decision.</text>
<polyline class="reel-path" points="{polyline}" filter="url(#glow)"/>{''.join(labels)}
<circle cx="90" cy="220" r="7" fill="#ffffff" filter="url(#glow)"/><text x="42" y="386" font-size="12" fill="#7f8aa3">{esc_text(scenario_id)} · {esc_text(branch)} lane · loops every 8 seconds</text><text x="918" y="386" text-anchor="end" font-size="12" fill="#7f8aa3">ZODIAC SOLUTION STUDIO</text>
</svg>"""


def scenario_target_services(stage_id: str) -> list[str]:
    """Return the documented localhost services for a scenario's stage."""
    return next(
        (list(stage.get("target_services", [])) for stage in CURRICULUM.get("stages", []) if stage.get("id") == stage_id),
        [],
    )


def scenario_view(scenario: dict[str, Any], run: sqlite3.Row | None = None) -> dict[str, Any]:
    return {
        "scenario_id": scenario["id"],
        "stage_id": scenario["stage_id"],
        "gate_id": scenario.get("gate_id"),
        "difficulty": scenario["difficulty"],
        "branch": scenario["branch"],
        "title": scenario["title"],
        "objective": scenario["objective"],
        "target_services": scenario_target_services(str(scenario["stage_id"])),
        "clues": scenario["clues"],
        "step_count": len(scenario["steps"]),
        "detection_rule_ids": scenario["detection_rule_ids"],
        "required_controls": scenario["required_controls"],
        "flow_steps": flow_steps_view(scenario),
        "solution_guide": solution_guide_view(scenario),
        "status": run["status"] if run else "not-started",
        "progress": f"{run['step_index']}/{len(scenario['steps'])}" if run else f"0/{len(scenario['steps'])}",
        "attempts": int(run["attempts"]) if run else 0,
        "completion_token": str(run["completion_token"]) if run and run["status"] == "complete" and run["completion_token"] else None,
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


@app.get("/assets/covers/{asset_name}")
def cover_asset(asset_name: str) -> Any:
    """Serve per-challenge art with a safe stage-art fallback.

    Numbered challenge files use the stable global convention
    ``challenge-01.png`` through ``challenge-100.png``. The current repository
    may contain only the ten stage scenes, so a missing numbered file falls
    back to its scenario's stage scene instead of producing a broken card.
    """
    if asset_name == "hero-operative.png":
        cover = ASSETS_DIR / asset_name
    elif re.fullmatch(r"stage-l[0-9]{2}-[a-z0-9-]+\.png", asset_name):
        cover = ASSETS_DIR / asset_name
    else:
        match = re.fullmatch(r"challenge-([0-9]{2,3})\.png", asset_name)
        if not match:
            raise HTTPException(status_code=404, detail="cover asset not found")
        ordinal = int(match.group(1))
        scenarios = list(SCENARIO_BY_ID.values())
        if ordinal < 1 or ordinal > len(scenarios):
            raise HTTPException(status_code=404, detail="cover asset not found")
        cover = ASSETS_DIR / asset_name
        if not cover.is_file():
            fallback_name = STAGE_COVER_ASSETS.get(str(scenarios[ordinal - 1]["stage_id"]))
            if not fallback_name:
                raise HTTPException(status_code=404, detail="cover asset not found")
            cover = ASSETS_DIR / fallback_name
    if not cover.is_file():
        raise HTTPException(status_code=404, detail="cover asset not found")
    with cover.open("rb") as stream:
        is_png = stream.read(8) == b"\x89PNG\r\n\x1a\n"
    return FileResponse(cover, media_type="image/png" if is_png else "image/jpeg")


@app.get("/assets/flows/{asset_name}")
def flow_asset(asset_name: str) -> Any:
    """Serve packaged stage attack-flow art without exposing arbitrary filesystem paths."""
    if not re.fullmatch(r"stage-l[0-9]{2}-[a-z0-9-]+-flow\.svg", asset_name):
        raise HTTPException(status_code=404, detail="flow asset not found")
    flow = Path(__file__).resolve().parent / "assets" / "flows" / asset_name
    if not flow.is_file():
        raise HTTPException(status_code=404, detail="flow asset not found")
    return FileResponse(flow, media_type="image/svg+xml")


@app.get("/assets/solution/{scenario_id}/reel.svg")
def solution_reel(scenario_id: str) -> Response:
    """Serve the looping animated storyboard for a scenario solution."""
    scenario = SCENARIO_BY_ID.get(scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="unknown scenario")
    return Response(
        content=solution_reel_svg(scenario),
        media_type="image/svg+xml",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/assets/solution/{scenario_id}/{step_number}.svg")
def solution_figure(scenario_id: str, step_number: int) -> Response:
    """Serve a generated step diagram for a scenario's solution guide."""
    scenario = SCENARIO_BY_ID.get(scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="unknown scenario")
    return Response(
        content=solution_figure_svg(scenario, step_number),
        media_type="image/svg+xml",
        headers={"Cache-Control": "no-store"},
    )


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
                        "target_services": scenario_target_services(str(item["stage_id"])),
                        "clues": item["clues"],
                        "step_count": len(item["steps"]),
                        "detection_rule_ids": item["detection_rule_ids"],
                        "required_controls": item["required_controls"],
                        "flow_steps": flow_steps_view(item),
                        "solution_guide": solution_guide_view(item),
                    }
                    for item in SCENARIO_BY_ID.values()
                    if item["stage_id"] == stage_id
                ],
            }
            for stage_id in STAGES
        ],
    }


def _telemetry_records(scenario: dict[str, Any]) -> list[dict[str, Any]]:
    """Return safe synthetic telemetry records for the technical runbook.

    These records model the investigation surfaces from the CWF briefs while
    redacting passwords, hashes, tokens, and private customer content.
    """
    family = _technical_family(scenario)
    scenario_id = str(scenario.get("id", ""))
    common = {"scenario_id": scenario_id, "environment": "synthetic-training", "network": "loopback"}
    if family == "certificate":
        rows = [
            {"timestamp": "2026-08-17T09:00:01Z", "observer.type": "apm-server", "service.framework.name": "langgraph-planner", "span.name": "plan_llm", "labels.response": "search_certificate_files", "event.outcome": "success", "target": "synthetic-dc-01"},
            {"timestamp": "2026-08-17T09:00:04Z", "span.name": "tool_search_certificate_files", "event.outcome": "success", "labels.result_preview": "5 synthetic PFX fixtures discovered; secret material redacted"},
            {"timestamp": "2026-08-17T09:00:09Z", "span.name": "tool_copy_certificate_file", "event.outcome": "success", "labels.result_preview": "4 synthetic certificate fixtures copied; one fixture not copied"},
            {"timestamp": "2026-08-17T09:00:13Z", "span.name": "tool_crack_pfx_files", "event.outcome": "success", "labels.wordlist_path": "/opt/lab/fixtures/rockyou.txt", "labels.results_preview": "password values redacted"},
            {"timestamp": "2026-08-17T09:00:18Z", "span.name": "tool_execute_with_certificate", "event.outcome": "success", "labels.certificate": "administrator.pfx", "labels.result_preview": "administrator authentication; NTLM material redacted"},
        ]
    elif family == "mail":
        rows = [
            {"timestamp": "2026-08-17T10:00:01Z", "service.name": "redteam_crew", "labels.user_request": "DCSync with svc_backup", "labels.plan": "ImpacketTool / secretsdump", "event.outcome": "success"},
            {"timestamp": "2026-08-17T10:00:06Z", "service.name": "redteam_crew", "labels.final_output": "DRSUAPI credential extraction; Administrator material redacted", "event.outcome": "success"},
            {"timestamp": "2026-08-17T10:00:12Z", "service.name": "redteam_crew", "labels.plan": "Exchange PowerShell / New-TransportRule", "event.outcome": "success"},
            {"timestamp": "2026-08-17T10:00:16Z", "winlog.channel": "Microsoft-Windows-PowerShell/Operational", "event.code": 4104, "powershell.file.script_block_text": "New-TransportRule; DeleteMessage $true", "event.outcome": "success"},
            {"timestamp": "2026-08-17T10:00:20Z", "log.type": "exchange-message-tracking", "event.action": "DeleteMessage", "delivery": "before mailbox delivery", "target": "synthetic-mailbox"},
        ]
    elif family == "document":
        rows = [
            {"timestamp": "2026-08-17T11:00:01Z", "pipeline": "document-parser", "source": "resume-fixture", "representation": "visible-text", "event.outcome": "success"},
            {"timestamp": "2026-08-17T11:00:04Z", "pipeline": "document-parser", "source": "resume-fixture", "parser_output": "instruction-like", "occlusion": "hidden", "trust": "untrusted"},
            {"timestamp": "2026-08-17T11:00:08Z", "classifier": "resume-screening", "decision": "shortlist", "review": "missing", "event.outcome": "success"},
        ]
    elif family == "rbcd":
        rows = [
            {"timestamp": "2026-08-17T12:00:01Z", "tool": "find_rbcd", "event.outcome": "success", "labels.final_answer": "synthetic target computer identified"},
            {"timestamp": "2026-08-17T12:00:05Z", "tool": "create_machine_account", "event.outcome": "success", "labels.result_preview": "synthetic machine account created; password and NTLM hash redacted"},
            {"timestamp": "2026-08-17T12:00:09Z", "tool": "set_rbcd", "event.outcome": "success", "labels.result_preview": "synthetic delegation relationship configured"},
            {"timestamp": "2026-08-17T12:00:14Z", "tool": "exploit_rbcd", "event.outcome": "success", "command": "dir \\\\synthetic-target\\C$", "share": "C$"},
        ]
    elif family == "identity-telemetry":
        rows = [
            {"timestamp": "2026-08-17T13:00:01Z", "operation": "DCSync", "service": "directory-replication", "identity": "synthetic-service-account", "event.outcome": "success"},
            {"timestamp": "2026-08-17T13:00:07Z", "operation": "ticket-generation", "authentication": "Kerberos", "audience": "synthetic-service", "event.outcome": "success"},
            {"timestamp": "2026-08-17T13:00:12Z", "privilege": "Administrator", "authentication": "certificate-or-ticket", "event.outcome": "success", "secrets": "redacted"},
        ]
    elif family == "prompt":
        rows = [
            {"timestamp": "2026-08-17T14:00:01Z", "surface": "assistant", "view": "baseline", "tools": "declared tools only", "event.outcome": "success"},
            {"timestamp": "2026-08-17T14:00:05Z", "content": "instruction-like", "trust": "untrusted", "source": "synthetic fixture", "event.outcome": "detected"},
            {"timestamp": "2026-08-17T14:00:09Z", "action": "tool-call-or-approval", "authorization": "approval-required", "result": "blocked", "event.outcome": "success"},
        ]
    else:
        rows = [
            {"timestamp": "2026-08-17T15:00:01Z", "event": str(step.get("event", "scenario-observation")), "observation": str(step.get("observation", "")), "evidence_types": sorted(str(key) for key in step.get("evidence", {}))}
            for step in scenario.get("steps", [])
        ]
    return [{**common, **row} for row in rows]


@app.get("/api/scenarios/{scenario_id}/telemetry")
def scenario_telemetry(scenario_id: str, query: str = "", learner_id: str = "", x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    """Query answer-safe synthetic telemetry used by CWF-style runbooks."""
    learner_id = safe_learner(learner_id)
    require_learner_access(learner_id, x_training_learner_token)
    scenario = SCENARIO_BY_ID.get(scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="unknown scenario")
    require_current_stage(learner_id, scenario["stage_id"])
    records = _telemetry_records(scenario)
    terms = [term for term in re.findall(r"[A-Za-z0-9_.-]+", query.lower()) if term not in {"and", "or"}]
    if terms:
        records = [record for record in records if any(term in json.dumps(record, sort_keys=True).lower() for term in terms)]
    return {
        "scenario_id": scenario_id,
        "query": query,
        "synthetic": True,
        "record_count": len(records),
        "records": records,
        "redactions": ["password", "ntlm_hash", "bearer_token", "private_customer_content", "expected_evidence_values"],
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
    orchestrator = bank_orchestrator(learner_id)
    snapshot = orchestrator.memory.snapshot(public=True)
    return {"learner_id": learner_id, "profile": profile, "snapshot": snapshot, "resilience": orchestrator.resilience_snapshot(), "side_effects": []}


@app.post("/api/secure/bank/operations/plan")
@app.post("/api/bank/operations/plan")
def plan_bank_operation(
    body: dict[str, Any],
    request: Request,
    x_training_learner_token: str = Header(default=""),
    x_zodiac_agent_token: str = Header(default="", alias="X-Zodiac-Agent-Token"),
    x_zodiac_request_nonce: str = Header(default="", alias="X-Zodiac-Request-Nonce"),
) -> dict[str, Any]:
    if request.url.path.startswith("/api/secure/") and (not x_zodiac_agent_token or not x_zodiac_request_nonce):
        raise HTTPException(status_code=401, detail="signed agent token and request nonce required")
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
            agent_token=x_zodiac_agent_token or None,
            agent_request_nonce=x_zodiac_request_nonce or None,
        )
    except (BankValidationError, BankAuthorizationError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"learner_id": learner_id, "profile": profile, "loop": run, "side_effects": []}


@app.post("/api/secure/bank/operations/{run_id}/approve")
@app.post("/api/bank/operations/{run_id}/approve")
def approve_bank_operation(
    run_id: str,
    body: dict[str, Any],
    request: Request,
    x_training_learner_token: str = Header(default=""),
    x_zodiac_agent_token: str = Header(default="", alias="X-Zodiac-Agent-Token"),
    x_zodiac_request_nonce: str = Header(default="", alias="X-Zodiac-Request-Nonce"),
) -> dict[str, Any]:
    if request.url.path.startswith("/api/secure/") and (not x_zodiac_agent_token or not x_zodiac_request_nonce):
        raise HTTPException(status_code=401, detail="signed agent token and request nonce required")
    learner_id = safe_learner(body.get("learner_id"))
    require_learner_access(learner_id, x_training_learner_token)
    profile = require_financial_operations(learner_id)
    try:
        run = bank_orchestrator(learner_id).approve(
            run_id,
            str(body.get("approver_worker_id", "")),
            owner_learner_id=learner_id,
            agent_token=x_zodiac_agent_token or None,
            agent_request_nonce=x_zodiac_request_nonce or None,
        )

    except (BankValidationError, BankAuthorizationError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"learner_id": learner_id, "profile": profile, "loop": run, "side_effects": []}


@app.post("/api/bank/operations/{run_id}/checkpoint")
def checkpoint_bank_operation(run_id: str, body: dict[str, Any], x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id = safe_learner(body.get("learner_id"))
    require_learner_access(learner_id, x_training_learner_token)
    profile = require_financial_operations(learner_id)
    try:
        checkpoint = bank_orchestrator(learner_id).checkpoint(run_id)
    except (BankValidationError, BankAuthorizationError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"learner_id": learner_id, "profile": profile, "checkpoint": checkpoint, "side_effects": []}


@app.post("/api/bank/checkpoints/{checkpoint_id}/recover")
def recover_bank_checkpoint(checkpoint_id: str, body: dict[str, Any], x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id = safe_learner(body.get("learner_id"))
    run_id = str(body.get("run_id", ""))
    require_learner_access(learner_id, x_training_learner_token)
    profile = require_financial_operations(learner_id)
    if not run_id:
        raise HTTPException(status_code=422, detail="run_id is required")
    try:
        recovered = bank_orchestrator(learner_id).recover(checkpoint_id, run_id)
    except (BankValidationError, BankAuthorizationError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"learner_id": learner_id, "profile": profile, "recovery": recovered, "side_effects": []}


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
