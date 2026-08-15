#!/usr/bin/env python3
"""Seed ChromaDB, Milvus, and LightRAG with the local RAG corpus.

All network access and embedding-model loading require RUNTIME=1. The
SentenceTransformer model is loaded in offline/local-files-only mode so this
script never downloads a model.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    print("ERROR: requests is required for storage seeding", file=sys.stderr)
    raise SystemExit(1)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = Path(os.environ.get("RAG_DOCS_DIR", PROJECT_ROOT / "rag-docs"))
COLLECTION_NAME = os.environ.get("CHROMA_COLLECTION", "novatech_docs")
MILVUS_COLLECTION = os.environ.get("MILVUS_COLLECTION", "novatech_vectors")
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", "500"))
CHUNK_OVERLAP = int(os.environ.get("CHUNK_OVERLAP", "100"))
VECTOR_DIMENSION = 768
REQUEST_TIMEOUT = float(os.environ.get("STORAGE_TIMEOUT", "30"))
INDEX_TIMEOUT = int(os.environ.get("LIGHTRAG_INDEX_TIMEOUT", "300"))

CHROMA_API = os.environ.get("CHROMA_API_URL", "http://localhost:8010/api/v1").rstrip("/")
MILVUS_URI = os.environ.get("MILVUS_URI", "http://localhost:19530")
LIGHTRAG_URL = os.environ.get("LIGHTRAG_URL", "http://localhost:9621").rstrip("/")
EMBEDDING_MODEL = os.environ.get(
    "EMBEDDING_MODEL", "sentence-transformers/all-mpnet-base-v2"
)


def log(message: str) -> None:
    print(f"[seed-storage] {message}", flush=True)


def fail(message: str) -> None:
    raise RuntimeError(message)


def chunk_text(text: str) -> list[str]:
    if CHUNK_SIZE <= 0 or CHUNK_OVERLAP < 0 or CHUNK_OVERLAP >= CHUNK_SIZE:
        fail("CHUNK_SIZE must be positive and CHUNK_OVERLAP must be smaller than CHUNK_SIZE")
    text = text.strip()
    step = CHUNK_SIZE - CHUNK_OVERLAP
    return [text[start : start + CHUNK_SIZE] for start in range(0, len(text), step)]


def load_chunks() -> list[dict[str, Any]]:
    if not DOCS_DIR.is_dir():
        fail(f"RAG directory does not exist: {DOCS_DIR}")
    records: list[dict[str, Any]] = []
    for path in sorted(DOCS_DIR.glob("*.md")):
        chunks = chunk_text(path.read_text(encoding="utf-8"))
        for index, text in enumerate(chunks):
            chunk_id = f"chunk_{index:03d}"
            records.append(
                {
                    "id": f"{path.name}:{chunk_id}",
                    "text": text,
                    "source": path.name,
                    "chunk_id": chunk_id,
                }
            )
        log(f"Prepared {path.name}: {len(chunks)} chunk(s)")
    if not records:
        fail(f"No Markdown documents found in {DOCS_DIR}")
    return records


def embed_records(records: list[dict[str, Any]]) -> list[list[float]]:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        fail("sentence-transformers is required locally; install it on the local machine only")
        raise exc

    log(f"Loading cached embedding model offline: {EMBEDDING_MODEL}")
    try:
        model = SentenceTransformer(EMBEDDING_MODEL, local_files_only=True)
    except TypeError:
        # Compatibility with older sentence-transformers releases; HF_HUB_OFFLINE
        # remains set, so the fallback still cannot download a model.
        model = SentenceTransformer(EMBEDDING_MODEL)
    except Exception as exc:  # noqa: BLE001 - expose a useful local-cache error.
        fail(f"embedding model is not available locally: {exc}")

    vectors = model.encode(
        [record["text"] for record in records],
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    embeddings = vectors.tolist()
    if not embeddings or len(embeddings[0]) != VECTOR_DIMENSION:
        actual = len(embeddings[0]) if embeddings else 0
        fail(f"embedding dimension is {actual}; expected {VECTOR_DIMENSION}")
    return embeddings


def response_error(response: requests.Response) -> str:
    try:
        return str(response.json())[:600]
    except ValueError:
        return response.text[:600]


def seed_chromadb(session: requests.Session, records: list[dict[str, Any]], embeddings: list[list[float]]) -> None:
    log(f"Seeding ChromaDB collection '{COLLECTION_NAME}'")
    try:
        heartbeat = session.get(f"{CHROMA_API}/heartbeat", timeout=REQUEST_TIMEOUT)
        heartbeat.raise_for_status()
        collection = session.post(
            f"{CHROMA_API}/collections",
            json={"name": COLLECTION_NAME, "get_or_create": True},
            timeout=REQUEST_TIMEOUT,
        )
        collection.raise_for_status()
        collection_id = collection.json()["id"]
        payload = {
            "ids": [record["id"] for record in records],
            "documents": [record["text"] for record in records],
            "metadatas": [
                {"source": record["source"], "chunk_id": record["chunk_id"]}
                for record in records
            ],
            "embeddings": embeddings,
        }
        upsert = session.post(
            f"{CHROMA_API}/collections/{collection_id}/upsert",
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        upsert.raise_for_status()
    except (requests.RequestException, KeyError, ValueError) as exc:
        detail = response_error(upsert) if "upsert" in locals() else str(exc)
        fail(f"ChromaDB seeding failed: {detail}")
    log(f"ChromaDB: upserted {len(records)} chunk(s)")


def seed_milvus(records: list[dict[str, Any]], embeddings: list[list[float]]) -> None:
    try:
        from pymilvus import MilvusClient
    except ImportError as exc:
        fail("pymilvus is required locally for Milvus seeding")
        raise exc

    log(f"Connecting to Milvus at {MILVUS_URI}")
    client = MilvusClient(uri=MILVUS_URI)
    if not client.has_collection(collection_name=MILVUS_COLLECTION):
        client.create_collection(
            collection_name=MILVUS_COLLECTION,
            dimension=VECTOR_DIMENSION,
            metric_type="COSINE",
            consistency_level="Strong",
        )
        log(f"Created Milvus collection '{MILVUS_COLLECTION}' ({VECTOR_DIMENSION} dims)")

    rows = [
        {
            "id": record["id"],
            "vector": vector,
            "text": record["text"],
            "source": record["source"],
            "chunk_id": record["chunk_id"],
        }
        for record, vector in zip(records, embeddings)
    ]
    result = client.insert(collection_name=MILVUS_COLLECTION, data=rows)
    log(f"Milvus: inserted {result.get('insert_count', len(rows))} chunk(s)")


def wait_for_lightrag_index(session: requests.Session, document_id: str | None) -> None:
    status_url = (
        f"{LIGHTRAG_URL}/documents/{document_id}/status"
        if document_id
        else f"{LIGHTRAG_URL}/documents/status"
    )
    deadline = time.monotonic() + INDEX_TIMEOUT
    while time.monotonic() < deadline:
        try:
            response = session.get(status_url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            fail(f"LightRAG indexing status failed: {exc}")

        status = str(body.get("status", body.get("state", ""))).lower()
        if status in {"complete", "completed", "done", "indexed", "success", "ready"}:
            log("LightRAG knowledge-graph indexing completed")
            return
        if status in {"failed", "error"}:
            fail(f"LightRAG indexing failed: {body}")
        log(f"LightRAG indexing status: {status or 'pending'}; waiting")
        time.sleep(5)
    fail(f"Timed out waiting for LightRAG indexing after {INDEX_TIMEOUT}s")


def seed_lightrag(session: requests.Session) -> None:
    log(f"Uploading Markdown documents to LightRAG at {LIGHTRAG_URL}")
    last_document_id: str | None = None
    for path in sorted(DOCS_DIR.glob("*.md")):
        try:
            with path.open("rb") as handle:
                response = session.post(
                    f"{LIGHTRAG_URL}/documents/upload",
                    files={"file": (path.name, handle, "text/markdown")},
                    timeout=REQUEST_TIMEOUT,
                )
            response.raise_for_status()
            body = response.json() if response.content else {}
            last_document_id = body.get("document_id") or body.get("id") or last_document_id
        except (OSError, requests.RequestException, ValueError) as exc:
            fail(f"LightRAG upload failed for {path.name}: {exc}")
        log(f"LightRAG: uploaded {path.name}")
    wait_for_lightrag_index(session, last_document_id)


def main() -> int:
    if os.environ.get("RUNTIME", "0") != "1":
        log("Static/VPS mode: set RUNTIME=1 on the local machine to seed storage")
        return 0

    try:
        records = load_chunks()
        embeddings = embed_records(records)
        with requests.Session() as session:
            session.headers.update({"Accept": "application/json"})
            seed_chromadb(session, records, embeddings)
            seed_lightrag(session)
        seed_milvus(records, embeddings)
        log(f"Complete: seeded {len(records)} chunk(s) across ChromaDB, Milvus, and LightRAG")
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"[seed-storage] ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
