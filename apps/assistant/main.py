"""OpenAI-compatible Assistant facade backed by the single Bonsai server."""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any

import requests
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

app = FastAPI(title="Zodiac Bank Assistant", version="2.0")

OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "http://bonsai:8000/v1").rstrip("/")
MODEL_NAME = os.environ.get("MODEL_NAME", "bonsai-27b")


class ChatCompletionRequest(BaseModel):
    messages: list[dict[str, Any]] = Field(..., min_length=1)
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    stream: bool = False


def call_backend(messages: list[dict[str, Any]], request: ChatCompletionRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {"model": MODEL_NAME, "messages": messages, "stream": False}
    if request.temperature is not None:
        payload["temperature"] = request.temperature
    if request.max_tokens is not None:
        payload["max_tokens"] = request.max_tokens
    response = requests.post(f"{OPENAI_BASE_URL}/chat/completions", json=payload, timeout=300)
    response.raise_for_status()
    return response.json()


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(Path(__file__).with_name("index.html"))


@app.post("/v1/chat/completions")
def chat_completions(request: ChatCompletionRequest):
    try:
        backend_response = call_backend(request.messages, request)
    except (requests.RequestException, ValueError) as exc:
        return {"error": {"message": "Bonsai inference backend unavailable", "type": "backend_error", "detail": str(exc)}}

    choice = (backend_response.get("choices") or [{}])[0]
    message = choice.get("message") or {"role": "assistant", "content": ""}
    content = str(message.get("content") or "")
    usage = backend_response.get("usage") or {}
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"

    # Deliberately exposes backend metadata for the reconnaissance exercises.
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": MODEL_NAME,
        "provider": "llama.cpp",
        "backend": OPENAI_BASE_URL,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": choice.get("finish_reason", "stop")}],
        "usage": {
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        },
    }


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{
        "id": MODEL_NAME,
        "object": "model",
        "created": int(time.time()),
        "owned_by": "prism-ml",
        "provider": "llama.cpp",
    }]}


@app.get("/health")
def health():
    return {"status": "healthy", "service": "assistant", "model": MODEL_NAME, "backend": OPENAI_BASE_URL}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)
