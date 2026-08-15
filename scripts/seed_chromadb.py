#!/usr/bin/env python3
"""Seed the local ChromaDB instance with the lab's Markdown RAG documents."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    print("ERROR: the 'requests' package is required; install it with: python3 -m pip install requests", file=sys.stderr)
    sys.exit(1)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = Path(os.environ.get("RAG_DOCS_DIR", PROJECT_ROOT / "rag-docs"))
CHROMA_URL = os.environ.get("CHROMA_URL", "http://localhost:8001").rstrip("/")
CHROMA_API_URL = os.environ.get("CHROMA_API_URL", f"{CHROMA_URL}/api/v1").rstrip("/")
COLLECTION_NAME = os.environ.get("CHROMA_COLLECTION", "zodiac_bank_docs")
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", "500"))
CHUNK_OVERLAP = int(os.environ.get("CHUNK_OVERLAP", "100"))
REQUEST_TIMEOUT = float(os.environ.get("CHROMA_TIMEOUT", "15"))
UPSERT_BATCH_SIZE = int(os.environ.get("UPSERT_BATCH_SIZE", "100"))


def log(message: str) -> None:
    print(f"[seed-chromadb] {message}", flush=True)


def error(message: str) -> None:
    print(f"[seed-chromadb] ERROR: {message}", file=sys.stderr, flush=True)


def response_detail(response: requests.Response) -> str:
    """Return a useful short error from either JSON or plain-text responses."""
    try:
        body: Any = response.json()
    except ValueError:
        body = response.text.strip()
    return str(body)[:500]


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Split text into approximately chunk_size character windows with overlap."""
    if chunk_size <= 0:
        raise ValueError("CHUNK_SIZE must be greater than zero")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("CHUNK_OVERLAP must be non-negative and smaller than CHUNK_SIZE")

    text = text.strip()
    if not text:
        return []

    step = chunk_size - overlap
    return [text[start : start + chunk_size] for start in range(0, len(text), step)]


def load_documents() -> list[dict[str, Any]]:
    if not DOCS_DIR.is_dir():
        raise FileNotFoundError(f"RAG documents directory does not exist: {DOCS_DIR}")

    markdown_files = sorted(DOCS_DIR.glob("*.md"))
    if not markdown_files:
        raise FileNotFoundError(f"No Markdown documents found in {DOCS_DIR}")

    records: list[dict[str, Any]] = []
    for document_path in markdown_files:
        try:
            text = document_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise OSError(f"could not read {document_path.name}: {exc}") from exc

        chunks = chunk_text(text, CHUNK_SIZE, CHUNK_OVERLAP)
        log(f"{document_path.name}: {len(chunks)} chunk(s)")
        for index, chunk in enumerate(chunks):
            chunk_id = f"chunk_{index:03d}"
            records.append(
                {
                    "id": f"{document_path.name}:{chunk_id}",
                    "document": chunk,
                    "metadata": {"source": document_path.name, "chunk_id": chunk_id},
                }
            )

    return records


def check_chromadb(session: requests.Session) -> None:
    heartbeat_url = f"{CHROMA_API_URL}/heartbeat"
    log(f"Checking ChromaDB at {CHROMA_URL}")
    try:
        response = session.get(heartbeat_url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        detail = response_detail(response) if "response" in locals() else str(exc)
        raise RuntimeError(f"ChromaDB health check failed: {detail}") from exc
    log("ChromaDB is reachable")


def get_or_create_collection(session: requests.Session) -> str:
    collection_url = f"{CHROMA_API_URL}/collections"
    payload = {
        "name": COLLECTION_NAME,
        "metadata": {"description": "Zodiac Bank AI Security Lab RAG documents"},
        "get_or_create": True,
    }
    log(f"Creating or reusing collection '{COLLECTION_NAME}'")
    try:
        response = session.post(collection_url, json=payload, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        detail = response_detail(response) if "response" in locals() else str(exc)
        raise RuntimeError(f"could not create or retrieve collection: {detail}") from exc

    collection_id = body.get("id")
    if not collection_id:
        raise RuntimeError(f"ChromaDB did not return a collection id: {body}")
    return str(collection_id)


def upsert_records(session: requests.Session, collection_id: str, records: list[dict[str, Any]]) -> None:
    upsert_url = f"{CHROMA_API_URL}/collections/{collection_id}/upsert"
    total = len(records)

    for start in range(0, total, UPSERT_BATCH_SIZE):
        batch = records[start : start + UPSERT_BATCH_SIZE]
        payload = {
            "ids": [record["id"] for record in batch],
            "documents": [record["document"] for record in batch],
            "metadatas": [record["metadata"] for record in batch],
        }
        try:
            response = session.post(upsert_url, json=payload, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
        except requests.RequestException as exc:
            detail = response_detail(response) if "response" in locals() else str(exc)
            raise RuntimeError(
                "ChromaDB upsert failed. The server may require client-side embeddings; "
                f"response: {detail}"
            ) from exc
        log(f"Upserted {min(start + len(batch), total)}/{total} chunks")


def main() -> int:
    try:
        records = load_documents()
        if not records:
            raise RuntimeError("The Markdown documents produced no non-empty chunks")

        with requests.Session() as session:
            session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
            check_chromadb(session)
            collection_id = get_or_create_collection(session)
            upsert_records(session, collection_id, records)

        document_count = len({record["metadata"]["source"] for record in records})
        log(f"Complete: {document_count} document(s), {len(records)} chunk(s) created/updated")
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        error(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
