"""Local Zodiac Bank graph/context engineering service.

The service builds a deterministic property graph from canonical synthetic data
and assembles bounded evidence packets for RAG and workflow consumers. It does
not authorize banking actions or call an external model.
"""

from __future__ import annotations

import hmac
import json
import os
import sys
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

sys.path.insert(0, "/app")
from context_engineering import assemble_context, render_for_model  # noqa: E402
from zodiac_graph import build_graph, neighborhood, validate_graph  # noqa: E402

BANK_PATH = Path("/app/bank-data/zodiac-bank.json")
WORKFLOW_PATH = Path("/app/bank-data/workflows.json")
DOCS_DIR = Path("/app/rag-docs")
DEFAULT_CONTEXT_API_KEY = "zodiac-bank-context-change-me"
CONTEXT_API_KEY = os.environ.get("GRAPH_CONTEXT_API_KEY", DEFAULT_CONTEXT_API_KEY)
SECURITY_MODE = os.environ.get("GRAPH_CONTEXT_SECURITY_MODE", "strict")

if SECURITY_MODE == "strict" and (CONTEXT_API_KEY == DEFAULT_CONTEXT_API_KEY or len(CONTEXT_API_KEY) < 24):
    raise RuntimeError("strict security requires GRAPH_CONTEXT_API_KEY with at least 24 characters")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


BANK = load_json(BANK_PATH)
WORKFLOWS = load_json(WORKFLOW_PATH)
GRAPH = build_graph(BANK, WORKFLOWS)
GRAPH_ERRORS = validate_graph(GRAPH)
if GRAPH_ERRORS:
    raise RuntimeError("invalid canonical graph: " + "; ".join(GRAPH_ERRORS))

app = FastAPI(title="Zodiac Bank Graph and Context Engineering", version="1.0")


@app.middleware("http")
async def security_headers(request: Any, call_next: Any) -> Any:
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def require_context_key(x_graph_context_key: str = Header(default="")) -> None:
    if SECURITY_MODE != "strict":
        return
    if not hmac.compare_digest(x_graph_context_key, CONTEXT_API_KEY):
        raise HTTPException(status_code=401, detail="graph context key required")


class ContextRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000)
    entity_ids: list[str] = Field(default_factory=list)
    scope_entity_ids: list[str] | None = None
    depth: int = 1
    max_nodes: int = 24
    max_chars: int = 12000


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "healthy",
        "service": "zodiac-bank-graph-context",
        "graph_id": GRAPH["graph_id"],
        "graph_schema_version": GRAPH["schema_version"],
        "nodes": GRAPH["node_count"],
        "edges": GRAPH["edge_count"],
        "documents": len(list(DOCS_DIR.glob("*.md"))),
        "scope": "localhost-only synthetic data",
        "security_mode": SECURITY_MODE,
        "authenticated_data_routes": SECURITY_MODE == "strict",
    }


@app.get("/v1/graph/neighborhood")
def graph_neighborhood(entity_id: str, depth: int = 1, max_nodes: int = 24, _: None = Depends(require_context_key)) -> dict[str, Any]:
    if not entity_id.strip():
        raise HTTPException(status_code=422, detail="entity_id is required")
    return neighborhood(GRAPH, [entity_id.strip().upper()], depth=depth, max_nodes=max_nodes)


@app.post("/v1/context/assemble")
def context_assemble(request: ContextRequest, _: None = Depends(require_context_key)) -> dict[str, Any]:
    if request.depth < 0 or request.depth > 3:
        raise HTTPException(status_code=422, detail="depth must be between 0 and 3")
    if request.max_nodes < 1 or request.max_nodes > 64:
        raise HTTPException(status_code=422, detail="max_nodes must be between 1 and 64")
    if request.max_chars < 1000 or request.max_chars > 16000:
        raise HTTPException(status_code=422, detail="max_chars must be between 1000 and 16000")
    packet = assemble_context(
        query=request.query,
        graph=GRAPH,
        docs_dir=DOCS_DIR,
        roots=request.entity_ids,
        allowed_entity_ids=request.scope_entity_ids,
        depth=request.depth,
        max_nodes=request.max_nodes,
        max_chars=request.max_chars,
    )
    return packet


@app.post("/v1/context/render")
def context_render(request: ContextRequest, _: None = Depends(require_context_key)) -> dict[str, Any]:
    packet = context_assemble(request)
    return {"packet_id": packet["packet_id"], "rendered": render_for_model(packet), "packet": packet}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5070)
