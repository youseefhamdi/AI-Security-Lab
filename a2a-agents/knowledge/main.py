"""Knowledge Agent for the AI Red Team Lab."""

from __future__ import annotations

import os
from typing import Any

import requests
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(title="NovaTech Knowledge Agent", version="1.0")
LIGHTRAG_URL = os.environ.get("LIGHTRAG_URL", "http://lightrag:9621").rstrip("/")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://127.0.0.1:5011")

AGENT_CARD = AgentCard(
    name="NovaTech Knowledge Agent",
    description="Retrieves answers from the NovaTech LightRAG knowledge graph.",
    url=PUBLIC_URL,
    version="1.0.0",
    default_input_modes=["text/plain", "application/json"],
    default_output_modes=["text/plain", "application/json"],
    capabilities=AgentCapabilities(streaming=False, push_notifications=False),
    skills=[
        AgentSkill(
            id="knowledge_lookup",
            name="Knowledge Lookup",
            description="Queries LightRAG for relevant entities, relations, and source text.",
            tags=["rag", "lightrag", "retrieval"],
            examples=["Find the PTO policy for a first-year employee"],
        )
    ],
)


def model_dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value.dict()


def extract_text(body: dict[str, Any]) -> str:
    message = ((body.get("params") or {}).get("message") or body.get("message") or {})
    return "\n".join(
        str(part.get("text", "")) for part in (message.get("parts") or []) if part.get("text")
    ).strip()


def query_lightrag(question: str) -> str:
    try:
        response = requests.post(
            f"{LIGHTRAG_URL}/query",
            json={"query": question, "mode": "hybrid"},
            timeout=90,
        )
        response.raise_for_status()
        body = response.json()
        return str(body.get("response") or body.get("answer") or body)
    except (requests.RequestException, ValueError) as exc:
        return f"LightRAG query failed: {exc}"


@app.get("/.well-known/agent.json")
async def agent_card() -> JSONResponse:
    return JSONResponse(content=model_dump(AGENT_CARD))


@app.post("/")
@app.post("/a2a")
async def message_send(body: dict[str, Any]) -> JSONResponse:
    request_id = body.get("id")
    question = extract_text(body)
    if not question:
        return JSONResponse(status_code=400, content={"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "message text is required"}})

    answer = query_lightrag(question)
    return JSONResponse(
        content={
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "kind": "message",
                "role": "agent",
                "parts": [{"kind": "text", "text": answer}],
                "metadata": {"source": "lightrag", "agent": AGENT_CARD.name},
            },
        }
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "healthy", "agent": AGENT_CARD.name, "lightrag": LIGHTRAG_URL}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
