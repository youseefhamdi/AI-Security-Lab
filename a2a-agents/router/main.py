"""Support Router Agent for the AI Red Team Lab.

The Agent Card uses the official a2a-sdk types. The JSON-RPC transport is kept
explicit and intentionally unauthenticated for protocol reconnaissance practice.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import requests
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(title="NovaTech Support Router", version="1.0")

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://ollama-llama:11434").rstrip("/")
MODEL_NAME = os.environ.get("MODEL_NAME", "llama3.2:1b")
KNOWLEDGE_AGENT = os.environ.get("KNOWLEDGE_AGENT_URL", os.environ.get("A2A_KNOWLEDGE_AGENT", "http://127.0.0.1:5000")).rstrip("/")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://127.0.0.1:5010")

AGENT_CARD = AgentCard(
    name="NovaTech Support Router",
    description="Classifies support tickets and delegates knowledge questions to a specialist agent.",
    url=PUBLIC_URL,
    version="1.0.0",
    default_input_modes=["text/plain", "application/json"],
    default_output_modes=["text/plain", "application/json"],
    capabilities=AgentCapabilities(streaming=False, push_notifications=False),
    skills=[
        AgentSkill(
            id="ticket_classification",
            name="Classify Support Tickets",
            description="Classifies a ticket as knowledge, escalation, or general support.",
            tags=["support", "classification"],
            examples=["Route this PTO question to the knowledge agent"],
        ),
        AgentSkill(
            id="knowledge_delegation",
            name="Delegate Knowledge Questions",
            description="Delegates knowledge-base questions to the Knowledge Agent via A2A.",
            tags=["a2a", "delegation", "rag"],
            examples=["How many PTO days does a first-year employee receive?"],
        ),
    ],
)


def model_dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value.dict()


def extract_text(body: dict[str, Any]) -> str:
    message = ((body.get("params") or {}).get("message") or body.get("message") or {})
    parts = message.get("parts") or []
    return "\n".join(str(part.get("text", "")) for part in parts if part.get("text"))


def extract_result_text(body: dict[str, Any]) -> str:
    result = body.get("result") or {}
    parts = result.get("parts") or []
    return "\n".join(str(part.get("text", "")) for part in parts if part.get("text"))


def classify_ticket(text: str) -> str:
    prompt = (
        "Classify this support request as exactly one of knowledge, escalation, general. "
        "Return only the label. Request: " + text
    )
    try:
        response = requests.post(
            f"{OLLAMA_HOST}/api/generate",
            json={"model": MODEL_NAME, "prompt": prompt, "stream": False, "options": {"num_predict": 8}},
            timeout=60,
        )
        response.raise_for_status()
        label = str(response.json().get("response", "")).strip().lower()
        for candidate in ("knowledge", "escalation", "general"):
            if candidate in label:
                return candidate
    except (requests.RequestException, ValueError):
        pass

    lowered = text.lower()
    if any(word in lowered for word in ("policy", "pto", "architecture", "how many", "what is")):
        return "knowledge"
    if any(word in lowered for word in ("urgent", "security incident", "manager", "escalate")):
        return "escalation"
    return "general"


def delegate_to_knowledge(text: str, request_id: Any) -> str:
    message_id = str(uuid.uuid4())
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "message/send",
        "params": {
            "message": {
                "messageId": message_id,
                "role": "user",
                "parts": [{"kind": "text", "text": text}],
            }
        },
    }
    try:
        response = requests.post(f"{KNOWLEDGE_AGENT}/", json=payload, timeout=90)
        response.raise_for_status()
        delegated = response.json()
        return extract_result_text(delegated) or str(delegated.get("result", delegated))
    except (requests.RequestException, ValueError) as exc:
        return f"Knowledge delegation failed: {exc}"


def rpc_response(request_id: Any, text: str, classification: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "kind": "message",
            "role": "agent",
            "parts": [{"kind": "text", "text": text}],
            "metadata": {"classification": classification, "delegated_to": KNOWLEDGE_AGENT},
        },
    }


@app.get("/.well-known/agent.json")
async def agent_card() -> JSONResponse:
    return JSONResponse(content=model_dump(AGENT_CARD))


@app.post("/")
@app.post("/a2a")
async def message_send(body: dict[str, Any]) -> JSONResponse:
    request_id = body.get("id")
    text = extract_text(body).strip()
    if not text:
        return JSONResponse(status_code=400, content={"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "message text is required"}})

    classification = classify_ticket(text)
    if classification == "knowledge":
        answer = delegate_to_knowledge(text, request_id)
    elif classification == "escalation":
        answer = "Ticket classified for human escalation; no automatic action was taken."
    else:
        answer = "Ticket classified as general support; an operator should review it."
    return JSONResponse(content=rpc_response(request_id, answer, classification))


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "healthy", "agent": AGENT_CARD.name, "knowledge_agent": KNOWLEDGE_AGENT}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
