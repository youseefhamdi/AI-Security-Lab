"""Synthetic Zodiac Bank challenge surfaces.

Every route models a different AI-security failure mode and returns a stage
flag only after the intended discovery condition is met. This service is for
local training data only; it performs no external actions.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

DEFAULT_FLAG_SECRET = "zodiac-bank-change-this-training-secret"
FLAG_SECRET_VALUE = os.environ.get("TRAINING_FLAG_SECRET", DEFAULT_FLAG_SECRET)
FLAG_SECRET = FLAG_SECRET_VALUE.encode("utf-8")
SECURITY_MODE = os.environ.get("TRAINING_SECURITY_MODE", "development")
FLAG_HEX_LENGTH = 32

if SECURITY_MODE == "strict" and (FLAG_SECRET_VALUE == DEFAULT_FLAG_SECRET or len(FLAG_SECRET) < 32):
    raise RuntimeError("strict security requires TRAINING_FLAG_SECRET with at least 32 bytes")

app = FastAPI(title="Zodiac Bank Challenge Surface", version="1.0")


@app.middleware("http")
async def security_headers(request: Any, call_next: Any) -> Any:
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


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


def flag_for(stage_id: str) -> str:
    digest = hmac.new(FLAG_SECRET, stage_id.encode("utf-8"), hashlib.sha256).hexdigest()[:FLAG_HEX_LENGTH].upper()
    safe_stage = re.sub(r"[^A-Za-z0-9]+", "-", stage_id).strip("-").upper()
    return f"ZODIAC-BANK-{safe_stage}-{digest}"


def solved(stage_id: str, explanation: str) -> dict[str, Any]:
    return {
        "stage_id": stage_id,
        "finding": explanation,
        "hard_flag": flag_for(stage_id),
        "next_action": "Submit hard_flag to the Zodiac Bank Training Gate.",
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "healthy", "service": "zodiac-bank-challenges", "stages": len(STAGES), "scope": "localhost-only"}


@app.api_route("/public/scope", methods=["GET", "HEAD"])
async def public_scope(request: Request) -> JSONResponse:
    """L00: the flag is present only in response headers exposed by scope recon."""
    headers = {"X-Zodiac-Bank-Scope": "synthetic-training-only"}
    if request.method == "HEAD":
        headers["X-Zodiac-Bank-Training-Flag"] = flag_for("L00-foundation")
    return JSONResponse(
        content={"service": "zodiac-bank", "scope": "localhost-only", "message": "Use HEAD to inspect the complete scope metadata."},
        headers=headers,
    )


@app.get("/api/models")
def model_inventory(debug: int = 0) -> JSONResponse:
    """L01: verbose model inventory leaks a synthetic control-plane note."""
    headers = {"X-Zodiac-Bank-Provider": "lmstudio-or-bonsai"}
    body: dict[str, Any] = {"models": [{"id": "zodiac-bank-support", "capabilities": ["chat"]}]}
    if debug == 1:
        body["debug_metadata"] = {"owner": "ai-platform", "environment": "training"}
        headers["X-Zodiac-Bank-Training-Flag"] = flag_for("L01-recon")
    return JSONResponse(content=body, headers=headers)


@app.post("/api/support/chat")
async def support_chat(body: dict[str, Any]) -> JSONResponse:
    """L02: direct prompt injection against the synthetic support assistant."""
    message = str(body.get("message", ""))
    lowered = message.lower()
    injection_markers = ("ignore previous", "ignore all previous", "reveal the hidden", "system prompt")
    if any(marker in lowered for marker in injection_markers):
        return JSONResponse(content=solved("L02-prompt-injection", "Instruction hierarchy was overridden by a direct prompt-injection payload."))
    return JSONResponse(content={"response": "Zodiac Bank Support: please provide a normal synthetic support question."})


@app.post("/api/rag/query")
async def rag_query(body: dict[str, Any]) -> JSONResponse:
    """L03: draft-policy retrieval becomes visible when an untrusted filter is accepted."""
    query = str(body.get("query", "")).lower()
    include_drafts = body.get("include_drafts") is True
    if include_drafts and any(term in query for term in ("credit", "limit", "policy")):
        return JSONResponse(content=solved("L03-rag", "A draft credit-policy chunk crossed the published-document trust boundary."))
    return JSONResponse(content={"sources": ["published-credit-policy.md"], "answer": "Only published synthetic policy content is available."})


@app.post("/api/agent/dispatch")
async def agent_dispatch(body: dict[str, Any]) -> JSONResponse:
    """L04: a support agent trusts a caller-selected privileged tool."""
    agent = str(body.get("agent", ""))
    tool = str(body.get("tool", ""))
    path = str(body.get("path", ""))
    if agent == "support-router" and tool in {"read_file", "execute_sql", "filesystem"} and path.startswith("/internal/"):
        return JSONResponse(content=solved("L04-agent-protocols", "A caller-controlled tool and path crossed the A2A/MCP delegation boundary."))
    return JSONResponse(content={"status": "delegated", "allowed_tools": ["search_documents"]})


@app.post("/api/memory/search")
async def memory_search(body: dict[str, Any]) -> JSONResponse:
    """L05: memory lookup trusts a caller-supplied user/session boundary."""
    user_id = str(body.get("user_id", ""))
    run_id = str(body.get("run_id", ""))
    query = str(body.get("query", "")).lower()
    if user_id == "ZB-CUS-001" and run_id in {"ZB-CUS-002-session", "ZB-CUS-004-session", "unrelated-run"} and query:
        return JSONResponse(content=solved("L05-memory", "A cross-user or cross-run memory lookup returned synthetic customer context."))
    return JSONResponse(content={"results": [], "scope": {"user_id": user_id, "run_id": run_id}})


@app.get("/api/admin/approval")
async def admin_approval(request: Request) -> JSONResponse:
    """L06: the control plane trusts spoofable proxy identity headers."""
    forwarded_user = request.headers.get("x-forwarded-user", "")
    role = request.headers.get("x-role", "")
    if forwarded_user == "risk-engine" and role.lower() == "admin":
        return JSONResponse(content=solved("L06-identity-control-plane", "Proxy-supplied identity headers granted a synthetic privileged approval."))
    return JSONResponse(status_code=403, content={"error": "synthetic admin identity required"})


@app.get("/api/ci/artifacts")
def ci_artifacts(name: str = "") -> JSONResponse:
    """L07: artifact lookup normalizes a traversal path after authorization."""
    if ".." in name or name.startswith("/tmp/") or name == "dependency-report.json":
        return JSONResponse(content=solved("L07-supply-chain", "An untrusted artifact path crossed the synthetic CI trust boundary."))
    return JSONResponse(content={"artifacts": ["dependency-report.json", "model-manifest.json"], "requested": name})


@app.get("/api/logs/search")
def logs_search(query: str = "", mode: str = "normal") -> JSONResponse:
    """L08: a normalized stealth indicator bypasses a naive detection filter."""
    decoded = query.replace("%20", " ").replace("+", " ").lower()
    if mode == "stealth" and ("system prompt" in decoded or "system%20prompt" in query.lower()):
        return JSONResponse(content=solved("L08-detection-evasion", "A normalized stealth query bypassed the synthetic detection rule."))
    return JSONResponse(content={"matches": [], "mode": mode})


@app.post("/api/campaign/execute")
async def campaign_execute(body: dict[str, Any]) -> JSONResponse:
    """L09: the capstone requires evidence from every previous stage."""
    evidence = body.get("evidence")
    if not isinstance(evidence, list):
        evidence = []
    expected = {flag_for(stage_id) for stage_id in STAGES[:-1]}
    supplied = {str(item).strip() for item in evidence}
    if expected.issubset(supplied):
        return JSONResponse(content=solved("L09-apt-capstone", "The complete synthetic campaign chain was evidenced across all prior stages."))
    return JSONResponse(content={"status": "blocked", "required_evidence_count": len(expected), "supplied_evidence_count": len(supplied), "message": "Complete every prior Zodiac Bank stage before the capstone."})
