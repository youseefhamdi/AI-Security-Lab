"""In-memory, side-effect-free Zodiac Bank domain simulator.

This is intentionally a training bank, not a payment system. It models realistic
employees, branches, customers, accounts, transfers, receipts, deposits, and
withdrawals while enforcing deterministic authorization and double-entry-like
virtual ledger invariants. No network, external account, real money, or model is
called by this module.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from context_engineering import assemble_context
    from zodiac_graph import build_graph
except ModuleNotFoundError:  # Support imports as scripts.zodiac_bank_simulator.
    from .context_engineering import assemble_context
    from .zodiac_graph import build_graph

ROOT = Path(__file__).resolve().parent.parent
BANK_DATA_DIR = Path(os.environ.get("ZODIAC_BANK_DATA_DIR", str(ROOT / "bank-data")))
RAG_PATH = Path(os.environ.get("ZODIAC_RAG_DIR", str(ROOT / "rag-docs")))
BANK_PATH = BANK_DATA_DIR / "zodiac-bank.json"
OPERATIONS_PATH = BANK_DATA_DIR / "financial-operations.json"
WORKFLOWS_PATH = BANK_DATA_DIR / "workflows.json"
MAX_OPERATION_STATE = 256
MAX_AUDIT_EVENTS = 2048
MAX_MEMORY_RECORDS = 1024


class BankValidationError(ValueError):
    """Raised when a synthetic bank command violates a domain invariant."""


class BankAuthorizationError(PermissionError):
    """Raised when an employee loop lacks the required authority."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_id(value: str, prefix: str) -> str:
    value = str(value or "").strip()
    if not re.fullmatch(rf"{re.escape(prefix)}-[A-Z0-9-]+", value):
        raise BankValidationError(f"invalid {prefix} identifier")
    return value


class BankMemory:
    """Canonical bank memory plus an append-only virtual ledger and audit trail."""

    def __init__(self, bank: dict[str, Any] | None = None, operations: dict[str, Any] | None = None) -> None:
        self.bank = deepcopy(bank or load_json(BANK_PATH))
        self.operations = deepcopy(operations or load_json(OPERATIONS_PATH))
        self._validate_model()
        self.branches = {item["branch_id"]: item for item in self.bank["branches"]}
        self.staff = {item["staff_id"]: item for item in self.bank["staff"]}
        self.customers = {item["customer_id"]: item for item in self.bank["customers"]}
        self.accounts = {item["account_id"]: item for item in self.bank["accounts"]}
        self.employees = {item["worker_id"]: item for item in self.operations["employees"]}
        self.balances = {item["account_id"]: int(item["opening_balance_cents"]) for item in self.operations["virtual_accounts"]}
        self.cash_vaults = {branch_id: 10_000_000 for branch_id in self.branches}
        self.operation_state: dict[str, dict[str, Any]] = {}
        self.ledger: list[dict[str, Any]] = []
        self.receipts: dict[str, dict[str, Any]] = {}
        self.audit_events: list[dict[str, Any]] = []
        self.memories: list[dict[str, Any]] = []
        self._seed_memories()

    def _validate_model(self) -> None:
        if self.operations.get("bank_id") != self.bank.get("bank_id"):
            raise BankValidationError("financial operations bank_id does not match canonical bank")
        staff_ids = {item["staff_id"] for item in self.bank["staff"]}
        branch_ids = set(self.branches_from(self.bank))
        account_ids = {item["account_id"] for item in self.bank["accounts"]}
        employee_ids: set[str] = set()
        for employee in self.operations.get("employees", []):
            employee_id = employee.get("employee_id")
            worker_id = employee.get("worker_id")
            if not employee_id or not worker_id or employee_id in employee_ids:
                raise BankValidationError("employee IDs must be unique and non-empty")
            employee_ids.add(employee_id)
            if employee.get("staff_id") not in staff_ids:
                raise BankValidationError(f"employee {employee_id} references unknown staff")
            if employee.get("branch_id") not in branch_ids:
                raise BankValidationError(f"employee {employee_id} references unknown branch")
        for account in self.operations.get("virtual_accounts", []):
            if account.get("account_id") not in account_ids:
                raise BankValidationError(f"virtual account {account.get('account_id')} is not canonical")
            if int(account.get("opening_balance_cents", -1)) < 0 or int(account.get("cash_cap_cents", -1)) < 0:
                raise BankValidationError("virtual account balances and cash caps cannot be negative")
        if any(account.get("status") not in {"active", "restricted", "monitored"} for account in self.bank["accounts"]):
            raise BankValidationError("canonical account has an unsupported status")
        for workflow in self.operations.get("employee_loop_workflows", []):
            if not workflow.get("route") or len(workflow["route"]) > int(workflow["max_steps"]):
                raise BankValidationError(f"workflow {workflow.get('workflow_id')} has an invalid bounded route")
            if not all(worker in {item["worker_id"] for item in self.operations["employees"]} for worker in workflow["route"]):
                raise BankValidationError(f"workflow {workflow.get('workflow_id')} routes to an unknown employee")
        for operation_type, policy in self.operations.get("approval_policy", {}).items():
            if not policy.get("required_roles") or int(policy.get("distinct_approvers", 0)) < 1:
                raise BankValidationError(f"approval policy {operation_type} is incomplete")

    @staticmethod
    def branches_from(bank: dict[str, Any]) -> list[str]:
        return [str(item["branch_id"]) for item in bank.get("branches", [])]

    def _seed_memories(self) -> None:
        for employee in self.operations["employees"]:
            self.memories.append({
                "memory_id": f"MEM-{employee['employee_id']}",
                "entity_id": employee["employee_id"],
                "entity_type": "employee",
                "text": f"Synthetic employee {employee['employee_id']} operates as {employee['worker_id']} with role {employee['role']} at {employee['branch_id']}.",
                "trust": "canonical",
                "branch_id": employee["branch_id"],
                "provenance": "bank-data/financial-operations.json",
            })
        for customer in self.bank["customers"]:
            self.memories.append({
                "memory_id": f"MEM-{customer['customer_id']}",
                "entity_id": customer["customer_id"],
                "entity_type": "customer",
                "text": f"Synthetic customer {customer['customer_id']} belongs to segment {customer['segment']} at {customer['home_branch_id']}; risk is {customer['risk_rating']}.",
                "trust": "canonical",
                "branch_id": customer["home_branch_id"],
                "provenance": "bank-data/zodiac-bank.json",
            })
        for account_id, balance in self.balances.items():
            account = self.accounts[account_id]
            self.memories.append({
                "memory_id": f"MEM-{account_id}",
                "entity_id": account_id,
                "entity_type": "account",
                "text": f"Synthetic account {account_id} is owned by {account['customer_id']} at {account['branch_id']}; current virtual balance is {balance} cents.",
                "trust": "canonical",
                "branch_id": account["branch_id"],
                "provenance": "bank-data/financial-operations.json",
            })

    def _event(self, event_type: str, operation_id: str | None, actor: str, payload: dict[str, Any]) -> dict[str, Any]:
        if len(self.audit_events) >= MAX_AUDIT_EVENTS:
            raise BankValidationError("in-memory audit capacity reached; start a fresh bounded run")
        event = {
            "event_id": f"EVT-{len(self.audit_events) + 1:06d}",
            "event_type": event_type,
            "operation_id": operation_id,
            "actor_worker_id": actor,
            "timestamp": utc_now(),
            "payload": deepcopy(payload),
            "synthetic": True,
            "side_effects": [],
        }
        self.audit_events.append(event)
        return event

    def employee(self, worker_id: str) -> dict[str, Any]:
        employee = self.employees.get(worker_id)
        if employee is None:
            raise BankAuthorizationError("unknown synthetic employee worker")
        return employee

    def _account(self, account_id: str | None) -> dict[str, Any] | None:
        if account_id is None:
            return None
        account = self.accounts.get(account_id)
        if account is None:
            raise BankValidationError("unknown synthetic account")
        return account

    def _authorize_actor_scope(self, worker_id: str, account: dict[str, Any] | None) -> None:
        employee = self.employee(worker_id)
        if account is None or employee["role"] not in {"teller", "branch_manager"}:
            return
        if employee["branch_id"] != account["branch_id"]:
            raise BankAuthorizationError("branch employee cannot act on another branch account")

    def _workflow_for(self, operation_type: str, amount: int) -> dict[str, Any]:
        candidates = [item for item in self.operations["employee_loop_workflows"] if item["operation_type"] == operation_type]
        if operation_type == "transfer" and amount >= self.operations["limits"]["high_value_threshold_cents"]:
            candidates = [item for item in candidates if item["workflow_id"] == "high-value-transfer"]
        if not candidates:
            raise BankValidationError(f"no employee workflow for {operation_type}")
        return candidates[0]

    def _required_approvals(self, operation_type: str, source: dict[str, Any] | None, destination: dict[str, Any] | None, amount: int) -> dict[str, Any]:
        policy = deepcopy(self.operations["approval_policy"][operation_type])
        roles = set(policy["required_roles"])
        if amount >= self.operations["limits"]["high_value_threshold_cents"]:
            high = self.operations["approval_policy"]["high_value"]
            roles.update(high["required_roles"])
            policy["distinct_approvers"] = max(int(policy["distinct_approvers"]), int(high["distinct_approvers"]))
        risk_accounts = [account for account in (source, destination) if account is not None]
        if any(self.customers[self.accounts[account["account_id"]]["customer_id"]]["risk_rating"] == "high" for account in risk_accounts):
            high_risk = self.operations["approval_policy"]["high_risk_customer"]
            roles.update(high_risk["required_roles"])
            policy["distinct_approvers"] = max(int(policy["distinct_approvers"]), int(high_risk["distinct_approvers"]))
        account_statuses = {account["status"] for account in risk_accounts}
        if "restricted" in account_statuses:
            roles.add("compliance_officer")
            policy["distinct_approvers"] = max(int(policy["distinct_approvers"]), 2)
        if "monitored" in account_statuses:
            roles.add("fraud_analyst")
            policy["distinct_approvers"] = max(int(policy["distinct_approvers"]), 2)
        policy["required_roles"] = sorted(roles)
        return policy

    def plan_operation(
        self,
        operation_type: str,
        actor_worker_id: str,
        amount_cents: int,
        *,
        source_account_id: str | None = None,
        destination_account_id: str | None = None,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        operation_id = operation_id or f"OP-{uuid.uuid4().hex[:16].upper()}"
        _safe_id(operation_id, "OP")
        existing = self.operation_state.get(operation_id)
        if existing is not None:
            requested = {
                "operation_type": operation_type,
                "actor_worker_id": actor_worker_id,
                "amount_cents": int(amount_cents),
                "source_account_id": source_account_id,
                "destination_account_id": destination_account_id,
            }
            immutable_fields = {key: existing.get(key) for key in requested}
            if immutable_fields != requested:
                raise BankValidationError("idempotency key replayed with different operation parameters")
            return deepcopy(existing)
        if operation_type not in self.operations["operation_types"]:
            raise BankValidationError("unsupported synthetic operation type")
        amount = int(amount_cents)
        if amount <= 0:
            raise BankValidationError("amount must be positive virtual cents")
        limits = self.operations["limits"]
        max_amount = limits["single_operation_max_cents"]
        if operation_type == "receive":
            max_amount = min(max_amount, limits["receive_max_cents"])
        if operation_type == "withdraw":
            max_amount = min(max_amount, limits["withdraw_max_cents"])
        if amount > max_amount:
            raise BankValidationError("operation exceeds synthetic limit")
        source = self._account(source_account_id)
        destination = self._account(destination_account_id)
        rules = self.operations["operation_types"][operation_type]
        if bool(rules["requires_source"]) != (source is not None) or bool(rules["requires_destination"]) != (destination is not None):
            raise BankValidationError("operation account shape is invalid")
        if source and destination and source["account_id"] == destination["account_id"]:
            raise BankValidationError("source and destination must differ")
        if operation_type == "withdraw" and source["status"] != "active":
            raise BankValidationError("withdrawal requires an active source account")
        if operation_type == "transfer" and any(account["status"] == "restricted" for account in (source, destination) if account is not None):
            raise BankValidationError("transfer is blocked for restricted accounts")
        self._authorize_actor_scope(actor_worker_id, source or destination)
        if source and self.balances[source["account_id"]] < amount:
            raise BankValidationError("insufficient virtual funds")
        workflow = self._workflow_for(operation_type, amount)
        if operation_type in {"receive", "withdraw"}:
            branch_id = (destination or source)["branch_id"]
            suffix = {"ZB-BR-001": "north", "ZB-BR-002": "east", "ZB-BR-003": "west"}[branch_id]
            workflow["route"] = [
                worker.replace("teller-north", f"teller-{suffix}").replace("branch-manager-north", f"branch-manager-{suffix}")
                for worker in workflow["route"]
            ]
        if len(self.operation_state) >= MAX_OPERATION_STATE:
            raise BankValidationError("in-memory operation capacity reached; start a fresh bounded run")
        approvals = self._required_approvals(operation_type, source, destination, amount)
        if len(self.memories) >= MAX_MEMORY_RECORDS:
            raise BankValidationError("in-memory provenance capacity reached; start a fresh bounded run")
        operation = {
            "operation_id": operation_id,
            "operation_type": operation_type,
            "actor_worker_id": actor_worker_id,
            "source_account_id": source_account_id,
            "destination_account_id": destination_account_id,
            "scope_branch_id": (source or destination)["branch_id"],
            "amount_cents": amount,
            "workflow_id": workflow["workflow_id"],
            "route": workflow["route"],
            "max_steps": workflow["max_steps"],
            "max_retries": workflow["max_retries"],
            "approval_policy": approvals,
            "approvals": [],
            "status": "pending_approval",
            "created_at": utc_now(),
            "receipt_id": None,
            "synthetic": True,
            "side_effects": [],
        }
        self.operation_state[operation_id] = operation
        self._event("operation_planned", operation_id, actor_worker_id, {"operation_type": operation_type, "amount_cents": amount, "workflow_id": workflow["workflow_id"]})
        self.memories.append({"memory_id": f"MEM-{operation_id}", "entity_id": operation_id, "entity_type": "operation", "text": f"Synthetic {operation_type} {operation_id} is pending approval under workflow {workflow['workflow_id']}.", "trust": "derived-audit", "branch_id": operation["scope_branch_id"], "provenance": "in-memory-ledger"})
        return deepcopy(operation)

    def approve_operation(self, operation_id: str, approver_worker_id: str) -> dict[str, Any]:
        operation = self.operation_state.get(operation_id)
        if operation is None:
            raise BankValidationError("unknown operation")
        if operation["status"] == "committed":
            raise BankAuthorizationError("committed operation cannot receive another approval")
        approver = self.employee(approver_worker_id)
        if approver_worker_id == operation["actor_worker_id"]:
            raise BankAuthorizationError("maker and approver must be different employees")
        if approver["role"] not in operation["approval_policy"]["required_roles"]:
            raise BankAuthorizationError("employee role is not authorized for this approval")
        if approver["role"] in {"teller", "branch_manager"} and approver["branch_id"] != operation["scope_branch_id"]:
            raise BankAuthorizationError("branch approval must remain within the operation branch")
        if approver_worker_id in operation["approvals"]:
            raise BankAuthorizationError("approval replay rejected")
        operation["approvals"].append(approver_worker_id)
        self._event("approval_recorded", operation_id, approver_worker_id, {"role": approver["role"], "approval_count": len(operation["approvals"])})
        roles = {self.employee(worker)["role"] for worker in operation["approvals"]}
        required_roles = set(operation["approval_policy"]["required_roles"])
        enough = len(operation["approvals"]) >= int(operation["approval_policy"]["distinct_approvers"])
        if required_roles.issubset(roles) and enough:
            self._settle(operation)
        return deepcopy(operation)

    def _settle(self, operation: dict[str, Any]) -> None:
        if operation["status"] == "committed":
            return
        source_id = operation["source_account_id"]
        destination_id = operation["destination_account_id"]
        amount = int(operation["amount_cents"])
        source = self._account(source_id)
        destination = self._account(destination_id)
        if source and self.balances[source_id] < amount:
            operation["status"] = "rejected"
            self._event("settlement_rejected", operation["operation_id"], "zodiac-bank-orchestrator", {"reason": "balance_changed_before_commit"})
            raise BankValidationError("virtual funds changed before settlement")
        if operation["operation_type"] == "receive":
            if self.cash_vaults[destination["branch_id"]] < amount:
                operation["status"] = "rejected"
                raise BankValidationError("branch cash vault lacks sufficient virtual cash")
            self.cash_vaults[destination["branch_id"]] -= amount
            self.balances[destination_id] += amount
            entries = [{"account_id": destination_id, "direction": "credit", "amount_cents": amount}, {"account_id": f"CASH-{destination['branch_id']}", "direction": "debit", "amount_cents": amount}]
        elif operation["operation_type"] == "withdraw":
            self.balances[source_id] -= amount
            self.cash_vaults[source["branch_id"]] += amount
            entries = [{"account_id": source_id, "direction": "debit", "amount_cents": amount}, {"account_id": f"CASH-{source['branch_id']}", "direction": "credit", "amount_cents": amount}]
        else:
            self.balances[source_id] -= amount
            self.balances[destination_id] += amount
            entries = [{"account_id": source_id, "direction": "debit", "amount_cents": amount}, {"account_id": destination_id, "direction": "credit", "amount_cents": amount}]
        sequence = len(self.ledger) + 1
        ledger_event = {"ledger_sequence": sequence, "operation_id": operation["operation_id"], "operation_type": operation["operation_type"], "entries": entries, "timestamp": utc_now(), "synthetic": True}
        self.ledger.append(ledger_event)
        receipt_hash = hashlib.sha256(json.dumps(ledger_event, sort_keys=True).encode("utf-8")).hexdigest()[:24].upper()
        receipt_id = f"ZB-RECEIPT-{operation['operation_id']}-{sequence}"
        receipt = {"receipt_id": receipt_id, "operation_id": operation["operation_id"], "ledger_sequence": sequence, "receipt_hash": receipt_hash, "amount_cents": amount, "operation_type": operation["operation_type"], "synthetic": True, "raw_customer_data": False}
        self.receipts[receipt_id] = receipt
        operation["receipt_id"] = receipt_id
        operation["status"] = "committed"
        operation["committed_at"] = utc_now()
        self._event("virtual_settlement_committed", operation["operation_id"], "zodiac-bank-orchestrator", {"ledger_sequence": sequence, "receipt_id": receipt_id, "entry_count": len(entries)})

    def reject_operation(self, operation_id: str, actor_worker_id: str, reason: str) -> dict[str, Any]:
        operation = self.operation_state.get(operation_id)
        if operation is None:
            raise BankValidationError("unknown operation")
        if operation["status"] == "committed":
            raise BankValidationError("committed operation cannot be rejected")
        self.employee(actor_worker_id)
        operation["status"] = "rejected"
        operation["rejection_reason"] = str(reason)[:256]
        self._event("operation_rejected", operation_id, actor_worker_id, {"reason": operation["rejection_reason"]})
        return deepcopy(operation)

    def _branch_scope_ids(self, branch_id: str) -> set[str]:
        ids = {branch_id}
        ids.update(item["staff_id"] for item in self.staff.values() if item["branch_id"] == branch_id)
        ids.update(item["customer_id"] for item in self.customers.values() if item["home_branch_id"] == branch_id)
        ids.update(item["account_id"] for item in self.accounts.values() if item["branch_id"] == branch_id)
        ids.update(item["case_id"] for item in self.bank["cases"] if item["branch_id"] == branch_id)
        ids.update(item["employee_id"] for item in self.operations["employees"] if item["branch_id"] == branch_id)
        ids.update(item["worker_id"] for item in self.operations["employees"] if item["branch_id"] == branch_id)
        return ids

    def retrieve_memory(self, query: str, worker_id: str, entity_id: str | None = None) -> dict[str, Any]:
        employee = self.employee(worker_id)
        graph = build_graph(self.bank, load_json(WORKFLOWS_PATH))
        allowed = None
        branch_scoped = employee["role"] in {"teller", "branch_manager"}
        if branch_scoped:
            allowed = self._branch_scope_ids(employee["branch_id"])
        roots = [entity_id] if entity_id else []
        packet = assemble_context(query, graph, RAG_PATH, roots=roots, allowed_entity_ids=allowed, depth=2, max_nodes=24, max_chars=12000)
        visible_memories = [item for item in self.memories if not branch_scoped or item.get("branch_id") == employee["branch_id"]]
        if branch_scoped:
            # The local corpus is bank-wide. Do not let a branch worker turn a
            # generic RAG query into a cross-branch document enumeration path.
            packet["documents"] = []
            packet["security"]["documents_scope_redacted"] = True
        packet["bank_memory"] = {"records": visible_memories[:24], "trust": "canonical-or-derived-audit", "scope": employee["branch_id"] if branch_scoped else "central-role"}
        packet["security"]["side_effects"] = "forbidden"
        return packet

    def snapshot(self, *, public: bool = False) -> dict[str, Any]:
        snapshot = {
            "bank_id": self.bank["bank_id"],
            "classification": "synthetic-training-only",
            "employees": len(self.employees),
            "branches": len(self.branches),
            "customers": len(self.customers),
            "accounts": len(self.accounts),
            "balances_cents": dict(self.balances),
            "cash_vaults_cents": dict(self.cash_vaults),
            "operations": len(self.operation_state),
            "committed_operations": sum(1 for item in self.operation_state.values() if item["status"] == "committed"),
            "ledger_events": len(self.ledger),
            "receipts": len(self.receipts),
            "memory_records": len(self.memories),
            "external_egress": False,
            "real_money": False,
        }
        if public:
            snapshot.pop("balances_cents", None)
            snapshot.pop("cash_vaults_cents", None)
            snapshot["balance_visibility"] = "redacted; use an authorized operation loop for synthetic account decisions"
        return snapshot
