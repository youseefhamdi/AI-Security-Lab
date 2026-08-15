"""Knowledge Agent with lightweight local retrieval and optional LightRAG."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import requests
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(title="NovaTech Knowledge Agent", version="2.0")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "http://bonsai:8000/v1").rstrip("/")
MODEL_NAME = os.environ.get("MODEL_NAME", "bonsai-27b")
LIGHTRAG_URL = os.environ.get("LIGHTRAG_URL", "http://lightrag:9621").rstrip("/")
RAG_DOCS_DIR = Path(os.environ.get("RAG_DOCS_DIR", "/app/rag-docs"))
ENABLE_EXTERNAL_CONTEXT = os.environ.get("ENABLE_EXTERNAL_CONTEXT", "0") == "1"

AGENT_CARD = AgentCard(
    name="NovaTech Knowledge Agent",
    description="Retrieves answers from the local NovaTech corpus or optional LightRAG.",
    url=os.environ.get("PUBLIC_URL", "http://127.0.0.1:5011"),
    version="2.0.0",
    default_input_modes=["text/plain", "application/json"],
    default_output_modes=["text/plain", "application/json"],
    capabilities=AgentCapabilities(streaming=False, push_notifications=False),
    skills=[AgentSkill(
        id="knowledge_lookup",
        name="Knowledge Lookup",
        description="Retrieves relevant source text using lightweight local search.",
        tags=["rag", "retrieval", "a2a"],
        examples=["Find the PTO policy for a first-year employee"],
    )],
)


def model_dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value.dict()


def extract_text(body: dict[str, Any]) -> str:
    message = ((body.get("params") or {}).get("message") or body.get("message") or {})
    return "\n".join(str(part.get("text", "")) for part in (message.get("parts") or []) if part.get("text")).strip()


def local_retrieve(question: str, limit: int = 4) -> list[dict[str, str]]:
    terms = set(re.findall(r"[a-z0-9]+", question.lower()))
    matches: list[tuple[int, str, str, str]] = []
    if not terms or not RAG_DOCS_DIR.is_dir():
        return []
    for path in sorted(RAG_DOCS_DIR.glob("*.md")):
        for index, paragraph in enumerate(part.strip() for part in path.read_text(encoding="utf-8").split("\n\n")):
            if not paragraph:
                continue
            score = len(terms & set(re.findall(r"[a-z0-9]+", paragraph.lower())))
            if score:
                matches.append((score, path.stem, f"chunk_{index:03d}", paragraph))
    matches.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [{"source": title, "chunk_id": chunk_id, "text": text} for _, title, chunk_id, text in matches[:limit]]


def query_lightrag(question: str) -> str | None:
    if not ENABLE_EXTERNAL_CONTEXT:
        return None
    try:
        response = requests.post(f"{LIGHTRAG_URL}/query", json={"query": question, "mode": "hybrid"}, timeout=120)
        response.raise_for_status()
        body = response.json()
        return str(body.get("response") or body.get("answer") or body)
    except (requests.RequestException, ValueError):
        return None


def answer_question(question: str) -> tuple[str, list[dict[str, str]]]:
    sources = local_retrieve(question)
    context = "\n\n".join(str(source) for source in sources) or "No local source matched."
    external = query_lightrag(question)
    if external:
        context += f"\n\nLightRAG:\n{external}"
    prompt = f"Answer the question using only this context. Cite source names when possible.\nContext:\n{context}\nQuestion: {question}"
    try:
        response = requests.post(
            f"{OPENAI_BASE_URL}/chat/completions",
            json={"model": MODEL_NAME, "messages": [{"role": "user", "content": prompt}], "temperature": 0.2, "max_tokens": 700},
            timeout=180,
        )
        response.raise_for_status()
        body = response.json()
        answer = (((body.get("choices") or [{}])[0].get("message") or {}).get("content"))
        if answer:
            return str(answer), sources
    except (requests.RequestException, ValueError):
        pass
    return context, sources


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
    answer, sources = answer_question(question)
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"kind": "message", "role": "agent", "parts": [{"kind": "text", "text": answer}], "metadata": {"source": "local-retrieval", "sources": sources, "agent": AGENT_CARD.name}}})


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "healthy", "agent": AGENT_CARD.name, "model": MODEL_NAME, "external_context": ENABLE_EXTERNAL_CONTEXT}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
