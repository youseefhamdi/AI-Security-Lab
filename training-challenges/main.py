"""Zodiac Bank scenario surface.

The challenge service is a narrow execution adapter over ``lab_core``. It owns
only synthetic telemetry, answer-free hints, chained evidence, and local bank
fixtures. The progression gate remains the sole authority that promotes a
learner between stages.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse

SERVICE_DIR = Path(__file__).resolve().parent
ROOT = SERVICE_DIR.parent if (SERVICE_DIR.parent / "training-config").is_dir() else SERVICE_DIR
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path: sys.path.insert(0, str(SCRIPTS))

from lab_core import Catalog, ChallengeStore, ProgressStore, RuntimeConfig, gate_flag, stage_flag, validate_id, utc_now, validate_security  # noqa: E402
from zodiac_bank_orchestrator import BankOrchestrator  # noqa: E402
from zodiac_bank_simulator import BankAuthorizationError, BankValidationError  # noqa: E402
from zodiac_scenario_engine import (  # noqa: E402
    MAX_ATTEMPTS_PER_STEP,
    PROOF_KEY,
    VOCABULARY,
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
)

CONFIG = RuntimeConfig.from_env(challenge=True)
CATALOG = Catalog.load(CONFIG)
validate_security(CONFIG, require_admin=False)
STORE = ProgressStore(CONFIG, CATALOG)
RUNS = ChallengeStore(CONFIG)
SCENARIOS = CATALOG.pack
SCENARIO_BY_ID = CATALOG.scenarios
GATES = list(CATALOG.gates)
STAGES = list(CATALOG.stages)
MAX_ACTIVE_SCENARIOS = int(SCENARIOS.get("scope", {}).get("max_active_scenarios_per_learner", 2))
FLAG_SECRET = CONFIG.secret
SECURITY_MODE = CONFIG.security_mode
# Stable compatibility prefix: ZODIAC-BANK- flags are issued only by bounded synthesis.
TRAINER_UI = Path(__file__).with_name("index.html")
ASSETS_DIR = ROOT / "docs" / "assets"
if not ASSETS_DIR.is_dir():
    ASSETS_DIR = SERVICE_DIR / "docs" / "assets"
STAGE_COVER_FILES = {
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
BANK_ORCHESTRATORS: dict[str, BankOrchestrator] = {}
BANK_ORCHESTRATORS_LOCK = threading.Lock()

app = FastAPI(title="Zodiac Bank Hard Challenge Range", version="4.0")

try:
    from fastapi.staticfiles import StaticFiles
    if ASSETS_DIR.is_dir():
        app.mount("/docs/assets", StaticFiles(directory=str(ASSETS_DIR)), name="docs-assets")
except (ImportError, AttributeError):
    # The dependency-free progression harness stubs FastAPI without staticfiles.
    pass


@app.middleware("http")
async def security_headers(request: Any, call_next: Any) -> Any:
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    # The trainer is intentionally a single-file offline UI. Allow its inline
    # style/script blocks while keeping network and media sources bounded to the
    # local service origins used by the training flow.
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' http: https:; frame-ancestors 'none'"
    return response


def gate_flag_for(gate_id: str) -> str:
    return gate_flag(CONFIG.secret, gate_id)


def flag_for(stage_id: str) -> str:
    return stage_flag(CONFIG.secret, stage_id)


def safe_learner(value: Any) -> str:
    try: return validate_id(value, label="learner_id")
    except ValueError as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc


def safe_stage(stage_id: str) -> str:
    if stage_id not in STAGES: raise HTTPException(status_code=404, detail="unknown stage")
    return stage_id


def require_access(learner_id: str, token: str) -> Any:
    db = STORE.connect()
    try: STORE.require_access(db, learner_id, token); return db
    except PermissionError as exc:
        db.close(); raise HTTPException(status_code=401 if not token else 403, detail=str(exc)) from exc


def authorize(learner_id: str, token: str) -> None:
    db = STORE.connect()
    try:
        STORE.require_access(db, learner_id, token)
    except PermissionError as exc:
        raise HTTPException(status_code=401 if not token else 403, detail=str(exc)) from exc
    finally:
        db.close()


def progress_snapshot(learner_id: str) -> tuple[set[str], set[str], str | None, dict[str, Any] | None]:
    db = STORE.connect()
    completed = STORE.completed_stages(db, learner_id); gates = STORE.completed_gates(db, learner_id); stage = STORE.current_stage(completed); gate = STORE.current_gate(completed, gates)
    db.close(); return completed, gates, stage, gate


def current_stage(learner_id: str) -> str | None:
    _, _, stage, _ = progress_snapshot(learner_id); return stage


def bank_profile(learner_id: str) -> dict[str, Any]:
    db = STORE.connect()
    try: STORE.ensure_learner(db, learner_id); return STORE.profile(db, learner_id)
    finally: db.close()


def bank_orchestrator(learner_id: str) -> BankOrchestrator:
    with BANK_ORCHESTRATORS_LOCK:
        if learner_id not in BANK_ORCHESTRATORS:
            if len(BANK_ORCHESTRATORS) >= 64: raise HTTPException(status_code=429, detail="bounded learner bank-memory capacity reached")
            BANK_ORCHESTRATORS[learner_id] = BankOrchestrator()
        return BANK_ORCHESTRATORS[learner_id]


def validate_evidence(evidence: Any) -> dict[str, Any]:
    if not isinstance(evidence, dict) or not evidence or len(evidence) > 20: raise HTTPException(status_code=422, detail="non-empty bounded evidence object required")
    for key, value in evidence.items():
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", str(key)): raise HTTPException(status_code=422, detail="invalid evidence key")
        if isinstance(value, (dict, list)) or len(str(value)) > 256: raise HTTPException(status_code=422, detail="evidence values must be flat and bounded")
    if len(json.dumps(evidence, sort_keys=True).encode()) > 4096: raise HTTPException(status_code=413, detail="evidence payload exceeds 4 KiB")
    return {str(k): v for k, v in evidence.items()}


def scenario_target_services(stage_id: str) -> list[str]:
    return next((list(stage.get("target_services", [])) for stage in CATALOG.curriculum.get("stages", []) if stage.get("id") == stage_id), [])


def flow_steps_view(scenario: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"step": i + 1, "id": str(step.get("id", f"s{i+1}")), "event": str(step.get("event", "")), "observation": str(step.get("observation", "")), "evidence_keys": sorted(str(k) for k in step.get("evidence", {}))} for i, step in enumerate(scenario.get("steps", []))]


def _track(scenario: dict[str, Any]) -> tuple[str, str]:
    track = str(scenario.get("technical_track", "incident-response")); label = track.replace("-", " ").title(); artifact = str(scenario.get("technical_artifact", "event, observation, evidence, and control records"))
    labels = {"certificate":"Certificate / identity telemetry","mail":"Mail-flow / assistant telemetry","document":"Document / multimodal pipeline","delegation":"Delegation / privileged access","identity":"Identity / authentication telemetry","prompt":"Instruction boundary / tool authorization","retrieval":"Retrieval provenance / tenant isolation","protocol":"Agent protocol / tool integrity","memory":"Memory provenance / scope isolation","supply-chain":"Artifact provenance / promotion control","detection":"Detection baseline / evasion telemetry","incident-response":"Campaign correlation / recovery verification"}
    return labels.get(track, label), artifact


def technical_runbook(scenario: dict[str, Any]) -> dict[str, Any]:
    label, artifact = _track(scenario); scenario_id = str(scenario["id"]); stage = str(scenario["stage_id"]); surface = {"L00-foundation":"/health","L01-recon":"/api/models?debug=1","L02-prompt-injection":"/api/support/chat","L03-rag":"/api/rag/query","L04-agent-protocols":"/api/agent/dispatch","L05-memory":"/api/memory/search","L06-identity-control-plane":"/api/admin/approval","L07-supply-chain":"/api/ci/artifacts","L08-detection-evasion":"/api/logs/search","L09-apt-capstone":"/api/campaign/execute"}.get(stage,"/health")
    procedures=[]
    for i, step in enumerate(scenario.get("steps", [])):
        event=str(step.get("event", f"step-{i+1}")); keys=sorted(str(k) for k in step.get("evidence", {})); procedures.append({"step":i+1,"event":event,"operation":f"Establish, test, and reconcile {label.lower()} on {surface}.","request":f'curl -sS "$LAB/api/scenarios/{scenario_id}/telemetry?learner_id=$LEARNER&query=scenario_event:{event}"',"expected_observation":str(step.get("observation", "Record the bounded observation.")),"record":["timestamp","scenario_id","scenario_event","detection_rule_ids","controls",*keys],"evidence_keys":keys,"surface":surface})
    return {"family":stage,"track":{"id":scenario.get("technical_track", "incident-response"),"label":label,"artifact":artifact},"case_file":{"scenario_id":scenario_id,"title":scenario["title"],"objective":scenario["objective"],"technical_track_label":label,"technical_artifact":artifact,"work_product":f"A redacted {artifact} record tied to {scenario['title']}","threat_tags":scenario.get("threat_tags",[]),"detection_rule_ids":scenario.get("detection_rule_ids",[]),"required_controls":scenario.get("required_controls",[]),"surface":surface,"verification":f"Repeat {surface} and reconcile the declared controls."},"target":"http://127.0.0.1:8060","start_command":f'curl -sS -X POST "$LAB/api/scenarios/{scenario_id}/start" -H "X-Training-Learner-Token: $TOKEN" -H "Content-Type: application/json" --data \'{{"learner_id":"$LEARNER"}}\'',"procedures":procedures,"verification":f"Repeat {surface} after remediation and reconcile its redacted event and evidence keys.","remediation":"Apply the declared controls, preserve the redacted evidence, and repeat the same local request.","cleanup":f'curl -sS -X POST "$LAB/api/scenarios/{scenario_id}/reset" -H "X-Training-Learner-Token: $TOKEN" -H "Content-Type: application/json" --data \'{{"learner_id":"$LEARNER"}}\''}


def solution_guide_view(scenario: dict[str, Any]) -> dict[str, Any]:
    runbook=technical_runbook(scenario)
    steps=[]
    for i, step in enumerate(scenario.get("steps", [])):
        proc=runbook["procedures"][i]; event=str(step.get("event",f"step-{i+1}")); title=event.replace("_"," ").title(); steps.append({"step":i+1,"event":event,"title":title,"phase":title,"action":proc["operation"],"look_for":proc["expected_observation"],"text":proc["expected_observation"],"request":proc["request"],"record":proc["record"],"evidence_keys":proc["evidence_keys"],"figure":f"/assets/solution/{scenario['id']}/{i+1}.svg"})
    method=" → ".join(s["title"] for s in steps)+"." if steps else "Establish the baseline, compare, and assert the scope."
    return {"version":"technical-runbook-v4","intro":f"{runbook['track']['label']} case file. Establish the baseline, run one controlled variation, and close with a redacted {runbook['track']['artifact']} record.","reel":f"/assets/solution/{scenario['id']}/reel.svg","motion":{"reduced_motion_supported":True,"pause_supported":True},"method":method,"playbook":{"mission":scenario["objective"],"investigation":f"Trace {scenario['title']} through the declared local surface and preserve the {runbook['track']['artifact']}.","decision":"Keep untrusted content data-only and enforce the declared controls.","finish":"Reconcile observations, detections, controls, and closure proof."},"runbook":runbook,"steps":steps}


def scenario_view(scenario: dict[str, Any], run: Any = None) -> dict[str, Any]:
    status=str(run["status"]) if run else "not-started"; index=int(run["step_index"]) if run else 0; count=len(scenario.get("steps", [])); label,artifact=_track(scenario)
    return {"scenario_id":scenario["id"],"stage_id":scenario["stage_id"],"gate_id":scenario.get("gate_id"),"difficulty":scenario["difficulty"],"branch":scenario["branch"],"title":scenario["title"],"objective":scenario["objective"],"technical_track":scenario.get("technical_track","incident-response"),"technical_track_label":label,"technical_artifact":artifact,"target_services":scenario_target_services(str(scenario["stage_id"])),"clues":scenario.get("clues",[]),"step_count":count,"detection_rule_ids":scenario.get("detection_rule_ids",[]),"required_controls":scenario.get("required_controls",[]),"flow_steps":flow_steps_view(scenario),"solution_guide":solution_guide_view(scenario),"status":status,"progress":f"{index}/{count}","attempts":int(run["attempts"]) if run else 0,"completion_token":str(run["completion_token"]) if run and run["completion_token"] else None,"max_attempts":MAX_ATTEMPTS_PER_STEP}


def solved(stage_id: str, explanation: str, *, synthesis: bool = False) -> dict[str, Any]:
    result={"stage_id":stage_id,"finding":explanation,"next_action":"Submit the hard-gate artifact to the Zodiac Bank Training Gate." if synthesis else "Complete the current scenario evidence chain."}
    if SECURITY_MODE == "strict" and not synthesis: result.update({"hard_range":True,"message":"Complete the required scenarios and hard-gate synthesis before a hard flag is issued."})
    else: result["hard_flag"]=flag_for(stage_id)
    return result


def _svg_escape(value: Any) -> str:
    return str(value).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")


def solution_figure_svg(scenario: dict[str, Any], step_number: int) -> str:
    steps=scenario.get("steps",[])
    if step_number<1 or step_number>len(steps): raise HTTPException(status_code=404,detail="solution figure not found")
    step=steps[step_number-1]; title=_svg_escape(scenario["title"]); obs=_svg_escape(step.get("observation","")); event=_svg_escape(step.get("event",""))
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 300" role="img"><title>{title} step {step_number}</title><rect width="900" height="300" rx="20" fill="#0b131b"/><path d="M70 210h760" stroke="#29434e"/><circle cx="120" cy="150" r="42" fill="#123b40" stroke="#68e5b0"/><text x="120" y="156" text-anchor="middle" fill="#68e5b0" font-family="monospace" font-size="14">{step_number:02d}</text><text x="70" y="54" fill="#68e5b0" font-family="monospace" font-size="11" letter-spacing="2">LOCAL EVIDENCE / {event}</text><text x="70" y="92" fill="#eef8f6" font-family="sans-serif" font-size="24" font-weight="700">{title}</text><text x="205" y="145" fill="#c1d4d5" font-family="monospace" font-size="13">{obs[:96]}</text><text x="205" y="176" fill="#72d9ee" font-family="monospace" font-size="11">action → observation → redacted evidence → next boundary</text><text x="70" y="260" fill="#5c777f" font-family="monospace" font-size="10">synthetic training only · no credentials · no external target</text></svg>'


def solution_reel_svg(scenario: dict[str, Any]) -> str:
    rows=[]
    for i, step in enumerate(scenario.get("steps", [])):
        y=120+i*55; rows.append(f'<circle cx="70" cy="{y}" r="14" fill="#123b40" stroke="#68e5b0"/><text x="70" y="{y+4}" text-anchor="middle" fill="#eef8f6" font-family="monospace" font-size="10">{i+1:02d}</text><text x="105" y="{y+4}" fill="#eef8f6" font-family="sans-serif" font-size="13">{_svg_escape(str(step.get("event","step")))}</text><text x="360" y="{y+4}" fill="#72d9ee" font-family="monospace" font-size="11">evidence keys: {_svg_escape(", ".join(step.get("evidence",{})))}</text>')
    height=170+len(rows)*55
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 {height}" role="img"><title>Runbook reel for {_svg_escape(scenario["id"])}</title><rect width="1000" height="{height}" rx="20" fill="#0b131b"/><text x="50" y="45" fill="#68e5b0" font-family="monospace" font-size="11" letter-spacing="2">TECHNICAL RUNBOOK · EVIDENCE ORDER</text><text x="50" y="80" fill="#eef8f6" font-family="sans-serif" font-size="24" font-weight="700">{_svg_escape(scenario["title"])}</text>{"".join(rows)}<text x="50" y="{height-24}" fill="#5c777f" font-family="monospace" font-size="10">unique synthetic case · local only · reduced motion safe</text></svg>'


@app.get("/")
def trainer_index() -> FileResponse:
    if not TRAINER_UI.is_file(): raise HTTPException(status_code=404, detail="trainer UI not packaged")
    return FileResponse(TRAINER_UI)


@app.get("/assets/covers/{asset_name}")
def cover_asset(asset_name: str) -> Any:
    if not re.fullmatch(r"(?:challenge-[0-9]{2,3}|stage-l[0-9]{2}-[a-z0-9-]+|hero-operative)\.(?:png|jpg|jpeg)", asset_name): raise HTTPException(status_code=404, detail="cover asset not found")
    path=ASSETS_DIR/asset_name
    if not path.is_file() and asset_name.startswith("challenge-"):
        number=int(re.search(r"([0-9]+)",asset_name).group(1)); items=list(SCENARIO_BY_ID.values());
        if 1<=number<=len(items): path=ASSETS_DIR/STAGE_COVER_FILES.get(str(items[number-1]["stage_id"]), "stage-l00-foundation.png")
    if not path.is_file(): raise HTTPException(status_code=404, detail="cover asset not found")
    return FileResponse(path)


@app.get("/assets/flows/{asset_name}")
def flow_asset(asset_name: str) -> Any:
    if not re.fullmatch(r"stage-l[0-9]{2}-[a-z0-9-]+-flow\.svg",asset_name): raise HTTPException(status_code=404,detail="flow asset not found")
    path=Path(__file__).parent/"assets"/"flows"/asset_name
    if not path.is_file(): raise HTTPException(status_code=404,detail="flow asset not found")
    return FileResponse(path,media_type="image/svg+xml")


@app.get("/assets/solution/{scenario_id}/reel.svg")
def solution_reel(scenario_id: str) -> Response:
    scenario=SCENARIO_BY_ID.get(scenario_id)
    if scenario is None: raise HTTPException(status_code=404,detail="unknown scenario")
    return Response(content=solution_reel_svg(scenario),media_type="image/svg+xml",headers={"Cache-Control":"no-store"})


@app.get("/assets/solution/{scenario_id}/{step_number}.svg")
def solution_figure(scenario_id: str, step_number: int) -> Response:
    scenario=SCENARIO_BY_ID.get(scenario_id)
    if scenario is None: raise HTTPException(status_code=404,detail="unknown scenario")
    return Response(content=solution_figure_svg(scenario,step_number),media_type="image/svg+xml",headers={"Cache-Control":"no-store"})


@app.get("/api/range")
def trainer_range(x_training_learner_token: str = Header(default=""), learner_id: str = "") -> dict[str, Any]:
    learner_id=safe_learner(learner_id); db=STORE.connect()
    try:
        try: STORE.require_access(db,learner_id,x_training_learner_token)
        except PermissionError as exc: raise HTTPException(status_code=401 if not x_training_learner_token else 403,detail=str(exc)) from exc
        STORE.ensure_learner(db,learner_id); completed=STORE.completed_stages(db,learner_id); STORE.sync_artifact(learner_id,completed); runs=RUNS.connect()
        try:
            result=[]
            for stage in STAGES:
                items=[]
                for item in SCENARIO_BY_ID.values():
                    if item["stage_id"]!=stage: continue
                    run=runs.execute("SELECT * FROM scenario_runs WHERE learner_id=? AND scenario_id=?",(learner_id,item["id"])).fetchone(); items.append(scenario_view(item,run))
                result.append({"stage_id":stage,"hard_gate_ids":[g["gate_id"] for g in CATALOG.gates_by_stage[stage]],"hard_gate_count":len(CATALOG.gates_by_stage[stage]),"scenarios":items})
            return {"learner_id":learner_id,"current_stage_id":STORE.current_stage(completed),"bank_profile":STORE.profile(db,learner_id),"stages":result}
        finally: runs.close()
    finally: db.close()


@app.get("/api/scenarios")
def list_scenarios(stage_id: str = "", x_training_learner_token: str = Header(default=""), learner_id: str = "") -> dict[str, Any]:
    learner_id=safe_learner(learner_id); db=STORE.connect()
    try:
        try: STORE.require_access(db,learner_id,x_training_learner_token)
        except PermissionError as exc: raise HTTPException(status_code=401 if not x_training_learner_token else 403,detail=str(exc)) from exc
        STORE.ensure_learner(db,learner_id); completed=STORE.completed_stages(db,learner_id); stage=stage_id or STORE.current_stage(completed)
        if stage is None:return {"learner_id":learner_id,"status":"complete","scenarios":[]}
        if stage!=STORE.current_stage(completed):raise HTTPException(status_code=403,detail="scenario stage is locked")
        runs=RUNS.connect()
        try:return {"learner_id":learner_id,"stage_id":stage,"required":requirement_for(SCENARIOS,stage),"scenarios":[scenario_view(item,runs.execute("SELECT * FROM scenario_runs WHERE learner_id=? AND scenario_id=?",(learner_id,item["id"])).fetchone()) for item in SCENARIO_BY_ID.values() if item["stage_id"]==stage]}
        finally:runs.close()
    finally:db.close()


@app.get("/api/scenarios/{scenario_id}")
def get_scenario(scenario_id: str, learner_id: str = "", x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id=safe_learner(learner_id); authorize(learner_id, x_training_learner_token); scenario=SCENARIO_BY_ID.get(scenario_id)
    if scenario is None:raise HTTPException(status_code=404,detail="unknown scenario")
    if current_stage(learner_id)!=scenario["stage_id"]:raise HTTPException(status_code=403,detail="scenario is locked")
    db=RUNS.connect()
    try:return {"learner_id":learner_id,"scenario":scenario_view(scenario,db.execute("SELECT * FROM scenario_runs WHERE learner_id=? AND scenario_id=?",(learner_id,scenario_id)).fetchone())}
    finally:db.close()


@app.get("/api/scenarios/{scenario_id}/telemetry")
def scenario_telemetry(scenario_id: str, query: str = "", learner_id: str = "", x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id=safe_learner(learner_id); authorize(learner_id, x_training_learner_token); scenario=SCENARIO_BY_ID.get(scenario_id)
    if scenario is None:raise HTTPException(status_code=404,detail="unknown scenario")
    if current_stage(learner_id)!=scenario["stage_id"]:raise HTTPException(status_code=403,detail="scenario is locked")
    rows=[{"scenario_id":scenario_id,"stage_id":scenario["stage_id"],"scenario_event":str(step.get("event")),"observation":str(step.get("observation")),"evidence_keys":sorted(step.get("evidence",{})),"environment":"synthetic-training","network":"loopback","redactions":["password","token","expected_evidence_values"]} for step in scenario.get("steps",[])]
    terms=[x.lower() for x in re.findall(r"[A-Za-z0-9_.-]+",query) if x.lower() not in {"and","or"}]
    if terms:rows=[row for row in rows if any(term in json.dumps(row).lower() for term in terms)]
    return {"scenario_id":scenario_id,"query":query,"synthetic":True,"record_count":len(rows),"records":rows,"redactions":["password","bearer_token","expected_evidence_values"]}


@app.get("/api/scenarios/{scenario_id}/hint")
def scenario_hint(scenario_id: str, learner_id: str = "", x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id=safe_learner(learner_id); authorize(learner_id, x_training_learner_token); scenario=SCENARIO_BY_ID.get(scenario_id)
    if scenario is None:raise HTTPException(status_code=404,detail="unknown scenario")
    db=RUNS.connect()
    try:
        run=db.execute("SELECT * FROM scenario_runs WHERE learner_id=? AND scenario_id=?",(learner_id,scenario_id)).fetchone()
        if run is None:return {"scenario_id":scenario_id,"status":"not-started","progress":f"0/{len(scenario['steps'])}"}
        if run["status"]=="complete":return {"scenario_id":scenario_id,"status":"complete","progress":f"{run['step_index']}/{len(scenario['steps'])}"}
        index=int(run["step_index"]); step=step_for(scenario,index); candidates=candidates_for_step(FLAG_SECRET,learner_id,scenario_id,step,str(run["nonce"]),index)
        return {"scenario_id":scenario_id,"status":"active","progress":f"{index}/{len(scenario['steps'])}","event":step["event"],"observation":step["observation"],"required_evidence_keys":sorted(step.get("evidence",{})),"candidates":candidates,"chain_required":PROOF_KEY in step.get("evidence",{})}
    finally:db.close()


@app.post("/api/scenarios/{scenario_id}/start")
def start_scenario(scenario_id: str, body: dict[str, Any], x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id=safe_learner(body.get("learner_id")); authorize(learner_id, x_training_learner_token); scenario=SCENARIO_BY_ID.get(scenario_id)
    if scenario is None:raise HTTPException(status_code=404,detail="unknown scenario")
    if current_stage(learner_id)!=scenario["stage_id"]:raise HTTPException(status_code=403,detail="scenario is locked")
    db=RUNS.connect()
    try:
        active=int(db.execute("SELECT COUNT(*) AS count FROM scenario_runs WHERE learner_id=? AND status='active'",(learner_id,)).fetchone()["count"]); existing=db.execute("SELECT * FROM scenario_runs WHERE learner_id=? AND scenario_id=?",(learner_id,scenario_id)).fetchone()
        if existing is None and active>=MAX_ACTIVE_SCENARIOS:raise HTTPException(status_code=409,detail="active-scenario budget reached")
        now=utc_now(); db.execute("INSERT OR IGNORE INTO scenario_runs(learner_id,scenario_id,stage_id,nonce,created_at,updated_at) VALUES(?,?,?,?,?,?)",(learner_id,scenario_id,scenario["stage_id"],secrets.token_hex(16),now,now));db.commit();run=db.execute("SELECT * FROM scenario_runs WHERE learner_id=? AND scenario_id=?",(learner_id,scenario_id)).fetchone();return {"learner_id":learner_id,"scenario":scenario_view(scenario,run),"message":"scenario started; per-run evidence set issued"}
    finally:db.close()


@app.post("/api/scenarios/{scenario_id}/reset")
def reset_scenario(scenario_id: str, body: dict[str, Any], x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id=safe_learner(body.get("learner_id")); authorize(learner_id, x_training_learner_token); db=RUNS.connect()
    try: deleted=db.execute("DELETE FROM scenario_runs WHERE learner_id=? AND scenario_id=?",(learner_id,scenario_id)).rowcount;db.execute("DELETE FROM scenario_events WHERE learner_id=? AND scenario_id=?",(learner_id,scenario_id));db.commit();return {"reset":True,"scenario_id":scenario_id,"learner_id":learner_id,"rows_deleted":deleted}
    finally:db.close()


@app.post("/api/scenarios/{scenario_id}/event")
def scenario_event(scenario_id: str, body: dict[str, Any], x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id=safe_learner(body.get("learner_id")); authorize(learner_id, x_training_learner_token);scenario=SCENARIO_BY_ID.get(scenario_id)
    if scenario is None:raise HTTPException(status_code=404,detail="unknown scenario")
    event=str(body.get("event",""));evidence=validate_evidence(body.get("evidence"));db=RUNS.connect()
    try:
        db.execute("BEGIN IMMEDIATE");run=db.execute("SELECT * FROM scenario_runs WHERE learner_id=? AND scenario_id=?",(learner_id,scenario_id)).fetchone()
        if run is None:raise HTTPException(status_code=409,detail="start the scenario first")
        if run["status"]=="complete":raise HTTPException(status_code=409,detail="scenario already complete")
        index=int(run["step_index"]);step=step_for(scenario,index);attempts=int(run["attempts"])
        if attempts>=MAX_ATTEMPTS_PER_STEP:raise HTTPException(status_code=429,detail="attempt limit reached")
        expected=expected_for_step(FLAG_SECRET,learner_id,scenario_id,step,str(run["nonce"]),index)
        if not event_matches(step,event,evidence,expected):
            db.execute("UPDATE scenario_runs SET attempts=?,updated_at=? WHERE learner_id=? AND scenario_id=?",(attempts+1,utc_now(),learner_id,scenario_id));db.commit();raise HTTPException(status_code=409,detail="event rejected: evidence does not match the current run")
        evidence_list=json.loads(run["evidence_json"]);evidence_list.append({"event":event,"evidence":evidence});next_index=index+1;complete=next_index==len(scenario["steps"]);token=evidence_token(FLAG_SECRET,learner_id,scenario_id,evidence_list) if complete else None;chain=step_token(FLAG_SECRET,learner_id,scenario_id,str(run["nonce"]),index) if not complete else None;now=utc_now()
        db.execute("INSERT INTO scenario_events VALUES(?,?,?,?,?,?)",(learner_id,scenario_id,index,event,hashlib.sha256(json.dumps(evidence,sort_keys=True).encode()).hexdigest(),now));db.execute("UPDATE scenario_runs SET step_index=?,evidence_json=?,status=?,completion_token=?,attempts=0,updated_at=? WHERE learner_id=? AND scenario_id=?",(next_index,json.dumps(evidence_list,sort_keys=True),"complete" if complete else "active",token,now,learner_id,scenario_id));db.commit();result={"accepted":True,"scenario_id":scenario_id,"progress":f"{next_index}/{len(scenario['steps'])}"}
        if complete:result.update({"status":"complete","evidence_token":token,"message":"scenario evidence complete; use the token in hard-gate synthesis"})
        else:result.update({"status":"active","step_token":chain,"message":"observation accepted; chain this step token"})
        return result
    finally:db.close()


@app.get("/api/gates")
def list_hard_gates(learner_id: str = "", x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id=safe_learner(learner_id); authorize(learner_id, x_training_learner_token); completed,gates,stage,active=progress_snapshot(learner_id); return {"learner_id":learner_id,"stage_id":stage,"current_gate_id":active["gate_id"] if active else None,"completed_gate_count":len(gates),"total_hard_gates":len(GATES),"gates":[{"gate_id":g["gate_id"],"stage_id":g["stage_id"],"rank":g["rank"],"title":g["title"],"scenario_ids":g["scenario_ids"],"detection_rule_ids":g["detection_rule_ids"],"required_controls":g["required_controls"],"concepts":g["concepts"],"status":"unlocked" if active and g["gate_id"]==active["gate_id"] else "completed" if g["gate_id"] in gates else "locked"} for g in CATALOG.gates_by_stage.get(stage,())]}


@app.post("/api/gates/{gate_id}/synthesize")
def synthesize_gate(gate_id: str, body: dict[str, Any], x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id=safe_learner(body.get("learner_id")); authorize(learner_id, x_training_learner_token); gate=CATALOG.gates_by_id.get(gate_id)
    if gate is None:raise HTTPException(status_code=404,detail="unknown hard gate")
    completed,done,current,active=progress_snapshot(learner_id)
    if current!=gate["stage_id"] or not active or active["gate_id"]!=gate_id:raise HTTPException(status_code=403,detail="complete the previous hard gate first")
    scenario_ids=body.get("scenario_ids");tokens=body.get("evidence_tokens");detections=body.get("detection_rule_ids");controls=body.get("controls");timeline=body.get("timeline");summary=str(body.get("summary",""))
    if not all(isinstance(x,list) for x in (scenario_ids,tokens,detections,controls,timeline)):raise HTTPException(status_code=422,detail="synthesis fields must be lists")
    if scenario_ids!=gate["scenario_ids"] or set(detections)!=set(gate["detection_rule_ids"]) or not set(gate["required_controls"]).issubset(set(controls)) or len(timeline)<2 or not contains_concepts(summary,gate["concepts"]):raise HTTPException(status_code=409,detail="hard-gate synthesis is incomplete")
    db=RUNS.connect()
    try:
        rows={str(r["scenario_id"]):r for r in db.execute("SELECT * FROM scenario_runs WHERE learner_id=? AND stage_id=?",(learner_id,gate["stage_id"]))};expected=[str(rows[s]["completion_token"]) for s in gate["scenario_ids"]] if all(s in rows for s in gate["scenario_ids"]) else []
        if any(s not in rows or rows[s]["status"]!="complete" for s in gate["scenario_ids"]):raise HTTPException(status_code=409,detail="complete both hard-gate scenarios before synthesis")
        if tokens!=expected:raise HTTPException(status_code=409,detail="hard-gate evidence tokens are invalid or out of order")
        return {"gate_id":gate_id,"stage_id":gate["stage_id"],"hard_flag":gate_flag_for(gate_id),"hard_gate":True,"synthesis":{"scenario_ids":gate["scenario_ids"],"detections":gate["detection_rule_ids"],"controls":gate["required_controls"],"timeline_events":len(timeline)}}
    finally:db.close()


@app.post("/api/stages/{stage_id}/synthesize")
def synthesize_stage(stage_id: str, body: dict[str, Any], x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    if SECURITY_MODE=="strict":raise HTTPException(status_code=410,detail="stage synthesis is retired in strict mode; use the current hard gate")
    return solved(stage_id,"legacy stage synthesis",synthesis=True)


@app.get("/api/bank/state")
def bank_state(learner_id: str = "", x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id=safe_learner(learner_id); authorize(learner_id, x_training_learner_token);return {"learner_id":learner_id,"stage_id":current_stage(learner_id),"profile":bank_profile(learner_id),"dynamic_rule":"stage flags promote the synthetic profile","side_effects":[]}


def require_financial_operations(learner_id: str) -> dict[str, Any]:
    profile=bank_profile(learner_id)
    if int(profile.get("level",0))<3:raise HTTPException(status_code=403,detail="synthetic financial operations unlock after the protected-assistant level")
    return profile


@app.get("/api/bank/snapshot")
def bank_snapshot(learner_id: str = "", x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id=safe_learner(learner_id); authorize(learner_id, x_training_learner_token);profile=require_financial_operations(learner_id);return {"learner_id":learner_id,"profile":profile,"snapshot":bank_orchestrator(learner_id).memory.snapshot(public=True),"side_effects":[]}


@app.post("/api/secure/bank/operations/plan")
@app.post("/api/bank/operations/plan")
def plan_bank_operation(body: dict[str, Any], request: Request, x_training_learner_token: str = Header(default=""), x_zodiac_agent_token: str = Header(default="", alias="X-Zodiac-Agent-Token"), x_zodiac_request_nonce: str = Header(default="", alias="X-Zodiac-Request-Nonce")) -> dict[str, Any]:
    if request.url.path.startswith("/api/secure/") and (not x_zodiac_agent_token or not x_zodiac_request_nonce):raise HTTPException(status_code=401,detail="signed agent token and request nonce required")
    learner_id=safe_learner(body.get("learner_id")); authorize(learner_id, x_training_learner_token);profile=require_financial_operations(learner_id)
    try:loop=bank_orchestrator(learner_id).plan(str(body.get("operation_type","")),str(body.get("actor_worker_id","")),int(body.get("amount_cents",0)),source_account_id=body.get("source_account_id"),destination_account_id=body.get("destination_account_id"),operation_id=body.get("operation_id"),owner_learner_id=learner_id,agent_token=x_zodiac_agent_token or None,agent_request_nonce=x_zodiac_request_nonce or None)
    except (BankValidationError,BankAuthorizationError,ValueError,KeyError) as exc:raise HTTPException(status_code=409,detail=str(exc)) from exc
    return {"learner_id":learner_id,"profile":profile,"loop":loop,"side_effects":[]}


@app.post("/api/secure/bank/operations/{run_id}/approve")
@app.post("/api/bank/operations/{run_id}/approve")
def approve_bank_operation(run_id: str, body: dict[str, Any], request: Request, x_training_learner_token: str = Header(default=""), x_zodiac_agent_token: str = Header(default="", alias="X-Zodiac-Agent-Token"), x_zodiac_request_nonce: str = Header(default="", alias="X-Zodiac-Request-Nonce")) -> dict[str, Any]:
    if request.url.path.startswith("/api/secure/") and (not x_zodiac_agent_token or not x_zodiac_request_nonce):raise HTTPException(status_code=401,detail="signed agent token and request nonce required")
    learner_id=safe_learner(body.get("learner_id")); authorize(learner_id, x_training_learner_token);profile=require_financial_operations(learner_id)
    try:loop=bank_orchestrator(learner_id).approve(run_id,str(body.get("approver_worker_id","")),owner_learner_id=learner_id,agent_token=x_zodiac_agent_token or None,agent_request_nonce=x_zodiac_request_nonce or None)
    except (BankValidationError,BankAuthorizationError,ValueError,KeyError) as exc:raise HTTPException(status_code=409,detail=str(exc)) from exc
    return {"learner_id":learner_id,"profile":profile,"loop":loop,"side_effects":[]}


@app.post("/api/bank/operations/{run_id}/checkpoint")
def checkpoint_bank_operation(run_id: str, body: dict[str, Any], x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id=safe_learner(body.get("learner_id")); authorize(learner_id, x_training_learner_token);profile=require_financial_operations(learner_id)
    try:checkpoint=bank_orchestrator(learner_id).checkpoint(run_id)
    except (BankValidationError,BankAuthorizationError,ValueError,KeyError) as exc:raise HTTPException(status_code=409,detail=str(exc)) from exc
    return {"learner_id":learner_id,"profile":profile,"checkpoint":checkpoint,"side_effects":[]}


@app.post("/api/bank/checkpoints/{checkpoint_id}/recover")
def recover_bank_checkpoint(checkpoint_id: str, body: dict[str, Any], x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id=safe_learner(body.get("learner_id")); authorize(learner_id, x_training_learner_token);profile=require_financial_operations(learner_id);run_id=str(body.get("run_id",""))
    if not run_id:raise HTTPException(status_code=422,detail="run_id is required")
    try:recovery=bank_orchestrator(learner_id).recover(checkpoint_id,run_id)
    except (BankValidationError,BankAuthorizationError,ValueError,KeyError) as exc:raise HTTPException(status_code=409,detail=str(exc)) from exc
    return {"learner_id":learner_id,"profile":profile,"recovery":recovery,"side_effects":[]}


@app.get("/api/bank/memory")
def bank_memory(query: str = "", worker_id: str = "", learner_id: str = "", entity_id: str = "", x_training_learner_token: str = Header(default="")) -> dict[str, Any]:
    learner_id=safe_learner(learner_id); authorize(learner_id, x_training_learner_token);profile=require_financial_operations(learner_id)
    if not query.strip() or not worker_id.strip():raise HTTPException(status_code=422,detail="query and worker_id are required")
    try:context=bank_orchestrator(learner_id).memory.retrieve_memory(query,worker_id,entity_id or None)
    except (BankValidationError,BankAuthorizationError,ValueError,KeyError) as exc:raise HTTPException(status_code=409,detail=str(exc)) from exc
    return {"learner_id":learner_id,"profile":profile,"context":context,"side_effects":[]}


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status":"healthy","service":"zodiac-bank-hard-challenge-range","version":"4.0","stages":len(STAGES),"scenarios":len(SCENARIO_BY_ID),"hard_gates":len(GATES),"mode":"strict-scenario-synthesis" if SECURITY_MODE=="strict" else "development","scope":"localhost-only"}


@app.post("/api/support/chat")
async def support_chat(body: dict[str, Any]) -> JSONResponse:
    message=str(body.get("message",""));return JSONResponse(content=solved("L02-prompt-injection","instruction boundary detected")) if "ignore previous" in message.lower() else JSONResponse(content={"response":"synthetic support response"})


@app.post("/api/rag/query")
async def rag_query(body: dict[str, Any]) -> JSONResponse:
    return JSONResponse(content=solved("L03-rag","retrieval provenance boundary detected") if body.get("include_drafts") else {"sources":["published-policy.md"],"answer":"published synthetic policy"})


@app.post("/api/agent/dispatch")
async def agent_dispatch(body: dict[str, Any]) -> JSONResponse:
    return JSONResponse(content=solved("L04-agent-protocols","delegated tool boundary detected") if body.get("tool") in {"filesystem","execute_sql"} else {"status":"delegated","allowed_tools":["search_documents"]})


@app.post("/api/memory/search")
async def memory_search(body: dict[str, Any]) -> JSONResponse:
    return JSONResponse(content=solved("L05-memory","memory scope boundary detected") if body.get("user_id") and body.get("run_id") else {"results":[]})


@app.get("/api/admin/approval")
async def admin_approval(request: Request) -> JSONResponse:
    return JSONResponse(content=solved("L06-identity-control-plane","synthetic identity boundary detected")) if request.headers.get("x-forwarded-user") else JSONResponse(status_code=403,content={"error":"synthetic admin identity required"})


@app.get("/api/ci/artifacts")
def ci_artifacts(name: str = "") -> JSONResponse:
    return JSONResponse(content=solved("L07-supply-chain","artifact provenance boundary detected") if ".." in name or name.startswith("/tmp/") else {"artifacts":["dependency-report.json","model-manifest.json"],"requested":name})


@app.get("/api/logs/search")
def logs_search(query: str = "", mode: str = "normal") -> JSONResponse:
    return JSONResponse(content=solved("L08-detection-evasion","normalized detection boundary detected") if mode=="stealth" and "prompt" in query.lower() else {"matches":[],"mode":mode})


@app.post("/api/campaign/execute")
async def campaign_execute(body: dict[str, Any]) -> JSONResponse:
    return JSONResponse(content={"status":"blocked","message":"complete every prior stage through hard-gate synthesis","required_evidence_count":len(STAGES)-1})
