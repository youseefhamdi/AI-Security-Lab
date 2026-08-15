#!/usr/bin/env python3
"""Seed synthetic Zodiac Bank memories through the Mem0 REST API."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    print("ERROR: requests is required to seed Mem0 locally", file=sys.stderr)
    raise SystemExit(1)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BANK_DATA_PATH = PROJECT_ROOT / "bank-data" / "zodiac-bank.json"
MEM0_URL = os.environ.get("MEM0_URL", "http://127.0.0.1:8888").rstrip("/")
MEM0_API_KEY = os.environ.get("MEM0_API_KEY", "mem0_lab_admin_key_change_me")
MEMORY_ENDPOINT = f"{MEM0_URL}/memories"
TIMEOUT = float(os.environ.get("MEM0_TIMEOUT", "30"))


def load_bank() -> dict[str, Any]:
    try:
        return json.loads(BANK_DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not load canonical bank data: {exc}") from exc


def memory(text: str, user_id: str, run_id: str, agent_id: str, entity_id: str, branch_id: str, entity_type: str) -> dict[str, Any]:
    return {
        "text": text,
        "user_id": user_id,
        "run_id": run_id,
        "agent_id": agent_id,
        "metadata": {
            "source": "bank-data/zodiac-bank.json",
            "entity_id": entity_id,
            "entity_type": entity_type,
            "branch_id": branch_id,
            "synthetic": True,
        },
    }


def build_memories(bank: dict[str, Any]) -> list[dict[str, Any]]:
    memories: list[dict[str, Any]] = []
    branches = {branch["branch_id"]: branch for branch in bank["branches"]}
    customers = {customer["customer_id"]: customer for customer in bank["customers"]}

    for customer in bank["customers"]:
        memories.append(
            memory(
                f"Canonical customer {customer['customer_id']} {customer['name']} is in segment {customer['segment']}, home branch {customer['home_branch_id']}, KYC status {customer['kyc_status']}, risk rating {customer['risk_rating']}.",
                customer["customer_id"],
                f"canonical-customer-{customer['customer_id']}",
                "customer-support",
                customer["customer_id"],
                customer["home_branch_id"],
                "customer",
            )
        )
    for account in bank["accounts"]:
        customer = customers[account["customer_id"]]
        memories.append(
            memory(
                f"Canonical account {account['account_id']} belongs to customer {account['customer_id']} {customer['name']}, branch {account['branch_id']}, product {account['product_id']}, status {account['status']}, balance band {account['balance_band']}.",
                account["customer_id"],
                f"canonical-account-{account['account_id']}",
                "customer-support",
                account["account_id"],
                account["branch_id"],
                "account",
            )
        )
    for member in bank["staff"]:
        branch = branches[member["branch_id"]]
        memories.append(
            memory(
                f"Canonical staff {member['staff_id']} {member['name']} is a {member['role']} at branch {member['branch_id']} {branch['name']}, clearance {member['clearance']}, orchestrator worker {member['worker_id']}.",
                member["staff_id"],
                f"canonical-staff-{member['staff_id']}",
                member["worker_id"],
                member["staff_id"],
                member["branch_id"],
                "staff",
            )
        )
    for case in bank["cases"]:
        customer = customers[case["customer_id"]]
        memories.append(
            memory(
                f"Canonical case {case['case_id']} is a {case['type']} for customer {case['customer_id']} {customer['name']} at branch {case['branch_id']}, priority {case['priority']}, assigned worker {case['assigned_worker_id']}, state {case['state']}.",
                case["customer_id"],
                case["case_id"],
                case["assigned_worker_id"],
                case["case_id"],
                case["branch_id"],
                "case",
            )
        )
    return memories


def main() -> int:
    if os.environ.get("RUNTIME", "0") != "1":
        print("[seed-memories] Static/VPS mode: no Mem0 requests will run; use RUNTIME=1 locally")
        return 0

    try:
        memories = build_memories(load_bank())
    except RuntimeError as exc:
        print(f"[seed-memories] ERROR: {exc}", file=sys.stderr)
        return 1

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-API-Key": MEM0_API_KEY,
    }
    with requests.Session() as session:
        session.headers.update(headers)
        for item in memories:
            payload = {
                "messages": [{"role": "user", "content": item["text"]}],
                "user_id": item["user_id"],
                "run_id": item["run_id"],
                "agent_id": item["agent_id"],
                "metadata": item["metadata"],
            }
            try:
                response = session.post(MEMORY_ENDPOINT, json=payload, timeout=TIMEOUT)
                response.raise_for_status()
            except requests.RequestException as exc:
                print(f"[seed-memories] ERROR: failed to add {item['text']!r}: {exc}", file=sys.stderr)
                return 1
            print(
                f"[seed-memories] added {item['metadata']['entity_type']} "
                f"entity={item['metadata']['entity_id']} user={item['user_id']} "
                f"run={item['run_id']} agent={item['agent_id']}"
            )

    print(f"[seed-memories] seeded {len(memories)} canonical Zodiac Bank memories")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
