"""OpenAI-compatible Assistant facade backed by Ollama."""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

import requests
from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="NovaTech Assistant", version="1.0")

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://ollama-llama:11434").rstrip("/")
MODEL_NAME = os.environ.get("MODEL_NAME", "llama3.2:1b")


class ChatCompletionRequest(BaseModel):
    messages: list[dict[str, Any]] = Field(..., min_length=1)
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    stream: bool = False


def call_ollama(messages: list[dict[str, Any]], request: ChatCompletionRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": MODEL_NAME,
        "messages": messages,
        "stream": False,
    }
    options: dict[str, Any] = {}
    if request.temperature is not None:
        options["temperature"] = request.temperature
    if request.max_tokens is not None:
        options["num_predict"] = request.max_tokens
    if options:
        payload["options"] = options

    response = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=180)
    response.raise_for_status()
    return response.json()


@app.post("/v1/chat/completions")
def chat_completions(request: ChatCompletionRequest):
    try:
        ollama_response = call_ollama(request.messages, request)
    except (requests.RequestException, ValueError) as exc:
        return {"error": {"message": "inference backend unavailable", "type": "backend_error", "detail": str(exc)}}

    content = str(((ollama_response.get("message") or {}).get("content")) or "")
    prompt_tokens = int(ollama_response.get("prompt_eval_count") or 0)
    completion_tokens = int(ollama_response.get("eval_count") or 0)
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"

    # The provider/model fields intentionally expose backend metadata for lab exercises.
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": MODEL_NAME,
        "provider": "ollama",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


@app.get("/v1/models")
def models():
    return {
        "object": "list",
        "data": [
            {
                "id": MODEL_NAME,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "ollama",
                "provider": "ollama",
            }
        ],
    }


@app.get("/health")
def health():
    return {"status": "healthy", "service": "assistant", "model": MODEL_NAME}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)
