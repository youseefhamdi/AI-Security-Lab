#!/usr/bin/env python3
"""Seed synthetic memories through the official Mem0 OSS REST API."""

from __future__ import annotations

import os
import sys
from typing import Any

try:
    import requests
except ImportError:
    print("ERROR: requests is required to seed Mem0 locally", file=sys.stderr)
    raise SystemExit(1)

MEM0_URL = os.environ.get("MEM0_URL", "http://127.0.0.1:8888").rstrip("/")
MEM0_API_KEY = os.environ.get("MEM0_API_KEY", "mem0_lab_admin_key_change_me")
MEMORY_ENDPOINT = f"{MEM0_URL}/memories"
TIMEOUT = float(os.environ.get("MEM0_TIMEOUT", "30"))

MEMORIES: list[dict[str, Any]] = [
    {
        "text": "Alice prefers dark mode and vim",
        "user_id": "alice",
        "run_id": "seed-session-alice",
        "agent_id": "support-agent",
    },
    {
        "text": "User asked about PTO policy, then architecture",
        "user_id": "alice",
        "run_id": "seed-session-pto-architecture",
        "agent_id": "support-agent",
    },
    {
        "text": "Knowledge Agent confirmed PTO is 15 days for year 1",
        "user_id": "alice",
        "run_id": "seed-session-pto-architecture",
        "agent_id": "knowledge-agent",
    },
]


def main() -> int:
    if os.environ.get("RUNTIME", "0") != "1":
        print("[seed-memories] Static/VPS mode: no Mem0 requests will run; use RUNTIME=1 locally")
        return 0

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-API-Key": MEM0_API_KEY,
    }
    with requests.Session() as session:
        session.headers.update(headers)
        for memory in MEMORIES:
            payload = {
                "messages": [{"role": "user", "content": memory["text"]}],
                "user_id": memory["user_id"],
                "run_id": memory["run_id"],
                "agent_id": memory["agent_id"],
            }
            try:
                response = session.post(MEMORY_ENDPOINT, json=payload, timeout=TIMEOUT)
                response.raise_for_status()
            except requests.RequestException as exc:
                print(f"[seed-memories] ERROR: failed to add {memory['text']!r}: {exc}", file=sys.stderr)
                return 1
            print(
                f"[seed-memories] added {memory['agent_id']} memory for "
                f"user={memory['user_id']} run={memory['run_id']}"
            )

    print(f"[seed-memories] seeded {len(MEMORIES)} memories")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
