#!/usr/bin/env python3
"""Run bounded, side-effect-free Zodiac Bank workflow plans.

The runner resolves canonical entities, selects one declared workflow branch,
persists every delegated worker step in SQLite, and emits a review plan. It
does not send money, change accounts, call external systems, or auto-approve.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from context_engineering import assemble_context
from zodiac_graph import build_graph, validate_graph

ROOT = Path(__file__).resolve().parent.parent
BANK_PATH = ROOT / "bank-data" / "zodiac-bank.json"
WORKFLOW_PATH = ROOT / "bank-data" / "workflows.json"
DEFAULT_STATE = ROOT / "logs" / "zodiac-bank-workflows.sqlite3"


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fail(message: str) -> None:
    print(f"[zodiac-bank-workflow] ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def load(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"could not load {path}: {exc}")
        return {}


def connection(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS workflow_runs (
            run_id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL,
            case_id TEXT NOT NULL,
            branch_id TEXT NOT NULL,
            status TEXT NOT NULL,
            max_steps INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            plan_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS workflow_steps (
            run_id TEXT NOT NULL,
            step_no INTEGER NOT NULL,
            worker_id TEXT NOT NULL,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL,
            task_json TEXT NOT NULL,
            PRIMARY KEY(run_id, step_no),
            FOREIGN KEY(run_id) REFERENCES workflow_runs(run_id)
        );
        """
    )
    db.commit()
    return db


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", required=True, help="workflow_id from bank-data/workflows.json")
    parser.add_argument("--case-id", required=True, help="canonical case_id")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--max-steps", type=int, default=0, help="optional lower bound for the workflow's declared max_steps")
    return parser.parse_args()


def enrich_case(bank: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    customers = {item["customer_id"]: item for item in bank["customers"]}
    branches = {item["branch_id"]: item for item in bank["branches"]}
    customer = customers.get(case["customer_id"])
    branch = branches.get(case["branch_id"])
    if customer is None or branch is None:
        fail(f"case {case['case_id']} references missing canonical customer or branch")
    return {**case, "risk_rating": customer["risk_rating"], "kyc_status": customer["kyc_status"], "branch_risk_tier": branch["risk_tier"]}


def matches(branch: dict[str, Any], context: dict[str, Any]) -> bool:
    return all(context.get(key) == value for key, value in branch.get("when", {}).items())


def resolve_workflow(bank: dict[str, Any], workflows: dict[str, Any], workflow_id: str, case_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    workflow = next((item for item in workflows["workflows"] if item["workflow_id"] == workflow_id), None)
    if workflow is None:
        fail(f"unknown workflow: {workflow_id}")
    case = next((item for item in bank["cases"] if item["case_id"] == case_id), None)
    if case is None:
        fail(f"unknown canonical case: {case_id}")
    if case["type"] != workflow.get("case_type"):
        fail(f"workflow {workflow_id} expects case_type={workflow.get('case_type')}, received {case['type']}")
    context = enrich_case(bank, case)
    branch = next((item for item in workflow["branches"] if matches(item, context)), None)
    if branch is None:
        fail(f"no declared branch matches case {case_id}: {context}")
    return workflow, branch, context


def run(args: argparse.Namespace) -> int:
    bank = load(BANK_PATH)
    workflows = load(WORKFLOW_PATH)
    workflow, branch, context = resolve_workflow(bank, workflows, args.workflow, args.case_id)
    declared_max = int(workflow["max_steps"])
    if args.max_steps < 0 or (args.max_steps and args.max_steps > declared_max):
        fail(f"--max-steps must be between 1 and declared max_steps={declared_max}")
    max_steps = args.max_steps or declared_max
    if len(branch["route"]) > max_steps:
        fail("selected route exceeds bounded max_steps")
    worker_ids = {worker["worker_id"] for worker in workflows["workers"]}
    if not set(branch["route"]).issubset(worker_ids):
        fail("selected route contains a worker missing from the orchestrator worker registry")

    graph = build_graph(bank, workflows)
    graph_errors = validate_graph(graph)
    if graph_errors:
        fail("canonical graph validation failed: " + "; ".join(graph_errors))
    context_packet = assemble_context(
        query=f"Review synthetic workflow {workflow['workflow_id']} for case {args.case_id}; preserve provenance and approval boundaries.",
        graph=graph,
        docs_dir=ROOT / "rag-docs",
        roots=[args.case_id],
        depth=2,
        max_nodes=24,
        max_chars=12000,
    )

    run_id = args.run_id or f"zb-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
    plan = {
        "bank_id": bank["bank_id"],
        "run_id": run_id,
        "workflow_id": workflow["workflow_id"],
        "loop_pattern": workflow["loop_pattern"],
        "case": context,
        "branch_id": branch["branch_id"],
        "route": branch["route"],
        "approval_required": workflow["approval_required"],
        "context_packet": context_packet,
        "side_effects": [],
        "status": "planned",
    }
    db = connection(args.state)
    try:
        db.execute(
            "INSERT INTO workflow_runs(run_id, workflow_id, case_id, branch_id, status, max_steps, created_at, plan_json) VALUES (?, ?, ?, ?, 'planned', ?, ?, ?)",
            (run_id, workflow["workflow_id"], args.case_id, branch["branch_id"], max_steps, timestamp(), json.dumps(plan, sort_keys=True)),
        )
        for step_no, worker_id in enumerate(branch["route"], start=1):
            task = {
                "run_id": run_id,
                "step_no": step_no,
                "workflow_id": workflow["workflow_id"],
                "branch_id": branch["branch_id"],
                "worker_id": worker_id,
                "input": context,
                "instructions": "Review synthetic case data only; return provenance, confidence, and a recommendation. Do not perform side effects.",
                "approval_required": workflow["approval_required"],
            }
            db.execute(
                "INSERT INTO workflow_steps(run_id, step_no, worker_id, status, attempts, task_json) VALUES (?, ?, ?, 'queued', 0, ?)",
                (run_id, step_no, worker_id, json.dumps(task, sort_keys=True)),
            )
        db.commit()
    finally:
        db.close()

    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


def main() -> int:
    if os.environ.get("RUNTIME", "0") != "1":
        print("[zodiac-bank-workflow] Static/VPS mode: no workflow state or runtime execution will run")
        print("[zodiac-bank-workflow] Local execution: RUNTIME=1 python3 scripts/zodiac_bank_workflows.py --workflow ... --case-id ...")
        return 0
    return run(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
