#!/usr/bin/env python3
"""Static validator for the canonical Zodiac Bank domain model."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from zodiac_graph import build_graph, validate_graph
from zodiac_bank_simulator import BankMemory, BankValidationError

ROOT = Path(__file__).resolve().parent.parent
BANK_PATH = ROOT / "bank-data" / "zodiac-bank.json"
WORKFLOW_PATH = ROOT / "bank-data" / "workflows.json"
FINANCIAL_OPERATIONS_PATH = ROOT / "bank-data" / "financial-operations.json"
ORCHESTRATOR_PATH = ROOT / "orchestrator-config" / "zodiac-bank.json"
CORPUS_PATH = ROOT / "rag-docs"


def fail(errors: list[str]) -> None:
    if errors:
        for error in errors:
            print(f"[zodiac-bank-validate] ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)


def load(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[zodiac-bank-validate] ERROR: cannot load {path}: {exc}", file=sys.stderr)
        raise SystemExit(1)


def ids(records: list[dict[str, Any]], key: str, label: str, errors: list[str]) -> set[str]:
    values = [record.get(key) for record in records]
    if any(not isinstance(value, str) or not value for value in values):
        errors.append(f"{label} contains a missing {key}")
    if len(values) != len(set(values)):
        errors.append(f"{label} contains duplicate {key} values")
    return {str(value) for value in values}


def main() -> int:
    bank = load(BANK_PATH)
    workflows = load(WORKFLOW_PATH)
    financial_operations = load(FINANCIAL_OPERATIONS_PATH)
    orchestrator = load(ORCHESTRATOR_PATH)
    errors: list[str] = []
    try:
        virtual_bank = BankMemory(bank, financial_operations)
        if len(virtual_bank.employees) != 12 or len(virtual_bank.balances) != 5:
            errors.append("financial operation model does not contain the expected employee/account set")
    except (BankValidationError, KeyError, TypeError, ValueError) as exc:
        errors.append(f"financial operation model failed validation: {exc}")

    branch_ids = ids(bank["branches"], "branch_id", "branches", errors)
    staff_ids = ids(bank["staff"], "staff_id", "staff", errors)
    customer_ids = ids(bank["customers"], "customer_id", "customers", errors)
    product_ids = ids(bank["products"], "product_id", "products", errors)
    account_ids = ids(bank["accounts"], "account_id", "accounts", errors)
    policy_ids = ids(bank["policies"], "policy_id", "policies", errors)
    case_ids = ids(bank["cases"], "case_id", "cases", errors)

    staff_by_id = {record["staff_id"]: record for record in bank["staff"]}
    customer_by_id = {record["customer_id"]: record for record in bank["customers"]}
    for branch in bank["branches"]:
        if branch["manager_staff_id"] not in staff_ids:
            errors.append(f"branch {branch['branch_id']} references unknown manager")
    for staff in bank["staff"]:
        if staff["branch_id"] not in branch_ids:
            errors.append(f"staff {staff['staff_id']} references unknown branch")
    for customer in bank["customers"]:
        if customer["home_branch_id"] not in branch_ids:
            errors.append(f"customer {customer['customer_id']} references unknown home branch")
    for account in bank["accounts"]:
        if account["customer_id"] not in customer_ids:
            errors.append(f"account {account['account_id']} references unknown customer")
        if account["branch_id"] not in branch_ids:
            errors.append(f"account {account['account_id']} references unknown branch")
        if account["product_id"] not in product_ids:
            errors.append(f"account {account['account_id']} references unknown product")
        elif account["branch_id"] != customer_by_id[account["customer_id"]]["home_branch_id"]:
            errors.append(f"account {account['account_id']} is assigned to a non-home branch")
    worker_ids = {record["worker_id"] for record in bank["staff"]}
    for policy in bank["policies"]:
        if policy["policy_id"] not in policy_ids:
            errors.append(f"invalid policy {policy}")
        if policy["owner_worker_id"] not in worker_ids and policy["owner_worker_id"] not in {worker["worker_id"] for worker in workflows["workers"]}:
            errors.append(f"policy {policy['policy_id']} references unknown owner worker")
    for case in bank["cases"]:
        if case["customer_id"] not in customer_ids or case["branch_id"] not in branch_ids:
            errors.append(f"case {case['case_id']} references unknown customer or branch")
        if case["assigned_worker_id"] not in {worker["worker_id"] for worker in workflows["workers"]}:
            errors.append(f"case {case['case_id']} references unknown assigned worker")
    all_entity_ids = branch_ids | staff_ids | customer_ids | product_ids | account_ids | policy_ids | case_ids
    for relationship in bank["relationships"]:
        if relationship["from"] not in all_entity_ids or relationship["to"] not in all_entity_ids:
            errors.append(f"relationship references unknown entity: {relationship}")

    workflow_workers = {worker["worker_id"] for worker in workflows["workers"]}
    if workflows["bank_id"] != bank["bank_id"] or orchestrator["bank_id"] != bank["bank_id"]:
        errors.append("bank_id is not symmetric across bank, workflow, and orchestrator manifests")
    if orchestrator["orchestrator_id"] != workflows["orchestrator_id"]:
        errors.append("orchestrator_id is not symmetric")
    if set(orchestrator["workers"]) != workflow_workers:
        errors.append("orchestrator workers do not match workflow workers")

    workflow_by_id = {workflow["workflow_id"]: workflow for workflow in workflows["workflows"]}
    manifest_by_id = {workflow["workflow_id"]: workflow for workflow in orchestrator["workflows"]}
    if set(workflow_by_id) != set(manifest_by_id):
        errors.append("orchestrator workflow IDs do not match canonical workflow IDs")
    for workflow in workflows["workflows"]:
        route_workers = {worker for branch in workflow["branches"] for worker in branch["route"]}
        if not route_workers.issubset(workflow_workers):
            errors.append(f"workflow {workflow['workflow_id']} routes to an unknown worker")
        branch_ids_for_workflow = [branch["branch_id"] for branch in workflow["branches"]]
        if len(branch_ids_for_workflow) != len(set(branch_ids_for_workflow)):
            errors.append(f"workflow {workflow['workflow_id']} has duplicate branch IDs")
        if workflow["max_steps"] < max(len(branch["route"]) for branch in workflow["branches"]):
            errors.append(f"workflow {workflow['workflow_id']} max_steps is smaller than a route")
        missing_delegates = route_workers - set(manifest_by_id.get(workflow["workflow_id"], {}).get("delegates_to", []))
        if missing_delegates:
            errors.append(f"orchestrator manifest misses delegates for {workflow['workflow_id']}: {sorted(missing_delegates)}")

    try:
        graph = build_graph(bank, workflows)
        errors.extend(f"graph: {error}" for error in validate_graph(graph))
    except (KeyError, TypeError, ValueError) as exc:
        errors.append(f"graph construction failed: {exc}")

    corpus_paths = sorted(CORPUS_PATH.glob("*.md")) if CORPUS_PATH.is_dir() else []
    if not corpus_paths:
        errors.append(f"canonical RAG corpus is missing: {CORPUS_PATH}")
    else:
        corpus = "\n".join(path.read_text(encoding="utf-8") for path in corpus_paths)
        for entity_id in sorted(all_entity_ids):
            if entity_id not in corpus:
                errors.append(f"RAG corpus is missing canonical entity {entity_id}")

    fail(errors)
    print(
        f"[zodiac-bank-validate] valid: {len(all_entity_ids)} entities, "
        f"{len(workflows['workflows'])} workflows, {len(workflow_workers)} workers, "
        f"{graph['node_count']} graph nodes, {graph['edge_count']} graph edges"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
