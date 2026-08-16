#!/usr/bin/env python3
"""Orchestrate bounded synthetic employee workflows over the virtual bank ledger.

The orchestrator is the only component allowed to advance a planned operation.
It creates an employee loop, attaches graph/RAG/memory evidence to every task,
requires explicit human-style approvals, and commits only an in-memory virtual
ledger event. It never calls a bank, payment rail, model, or external URL.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import threading
import os
import uuid
from functools import wraps
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from zodiac_agent_security import AgentSecurityError, ReplayGuard, verify_request
    from zodiac_bank_simulator import BankAuthorizationError, BankMemory, BankValidationError
    from zodiac_resilience import CheckpointStore, CircuitBreaker, KillSwitch, reconcile_ledger
except ModuleNotFoundError:  # Support imports as scripts.zodiac_bank_orchestrator.
    from .zodiac_agent_security import AgentSecurityError, ReplayGuard, verify_request
    from .zodiac_bank_simulator import BankAuthorizationError, BankMemory, BankValidationError
    from .zodiac_resilience import CheckpointStore, CircuitBreaker, KillSwitch, reconcile_ledger

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_AUDIT = ROOT / "logs" / "zodiac-bank-orchestrator.sqlite3"
MAX_ORCHESTRATOR_RUNS = 256
DEFAULT_AGENT_SIGNING_KEY = "zodiac-bank-agent-signing-key-change-me"
AGENT_SIGNING_KEY = os.environ.get("ZODIAC_AGENT_SIGNING_KEY", DEFAULT_AGENT_SIGNING_KEY)
AGENT_SECURITY_MODE = os.environ.get("ZODIAC_AGENT_SECURITY_MODE", "development").lower()
AGENT_REPLAY_GUARD = ReplayGuard()


def synchronized(method: Any) -> Any:
    @wraps(method)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def audit_connection(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS orchestrator_runs (
            run_id TEXT PRIMARY KEY,
            operation_id TEXT NOT NULL,
            workflow_id TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            result_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS orchestrator_loop_steps (
            run_id TEXT NOT NULL,
            step_no INTEGER NOT NULL,
            worker_id TEXT NOT NULL,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL,
            context_packet_id TEXT,
            recorded_at TEXT NOT NULL,
            PRIMARY KEY(run_id, step_no)
        );
        """
    )
    db.commit()
    return db


class BankOrchestrator:
    def __init__(self, memory: BankMemory | None = None, audit_path: Path | None = None) -> None:
        self.memory = memory or BankMemory()
        self.audit_path = audit_path
        self.runs: dict[str, dict[str, Any]] = {}
        self.checkpoints = CheckpointStore()
        self.circuit_breaker = CircuitBreaker()
        self.kill_switch = KillSwitch()
        self._lock = threading.RLock()

    def _authorize_agent_request(
        self,
        token: str | None,
        request_nonce: str | None,
        *,
        worker_id: str,
        capability: str,
        owner_learner_id: str | None,
    ) -> None:
        """Bind a delegated agent call to the exact worker and bank scope.

        Existing Python callers remain compatible in development mode. Secure
        service routes pass both values and therefore always receive signature,
        audience, capability, learner, branch, expiry, and replay checks.
        """
        if not token:
            if AGENT_SECURITY_MODE == "strict":
                raise BankAuthorizationError("signed agent token required")
            return
        employee = self.memory.employee(worker_id)
        try:
            verify_request(
                token,
                AGENT_SIGNING_KEY,
                AGENT_REPLAY_GUARD,
                request_nonce=str(request_nonce or ""),
                audience="zodiac-bank-orchestrator",
                required_capability=capability,
                subject=worker_id,
                branch_id=employee["branch_id"],
                learner_id=owner_learner_id,
            )
        except AgentSecurityError as exc:
            raise BankAuthorizationError(str(exc)) from exc

    @synchronized
    def plan(
        self,
        operation_type: str,
        actor_worker_id: str,
        amount_cents: int,
        *,
        source_account_id: str | None = None,
        destination_account_id: str | None = None,
        operation_id: str | None = None,
        owner_learner_id: str | None = None,
        agent_token: str | None = None,
        agent_request_nonce: str | None = None,
    ) -> dict[str, Any]:
        self.kill_switch.check()
        if not self.circuit_breaker.allow():
            raise BankValidationError("synthetic orchestrator circuit breaker is open")
        self._authorize_agent_request(
            agent_token,
            agent_request_nonce,
            worker_id=actor_worker_id,
            capability="bank.operation.plan",
            owner_learner_id=owner_learner_id,
        )
        if len(self.runs) >= MAX_ORCHESTRATOR_RUNS:
            raise BankValidationError("in-memory orchestrator run capacity reached; start a fresh bounded run")
        if operation_id is not None:
            for existing_run in self.runs.values():
                if existing_run.get("operation_id") == operation_id and existing_run.get("owner_learner_id") != owner_learner_id:
                    raise BankAuthorizationError("operation id belongs to another learner")
        operation = self.memory.plan_operation(
            operation_type,
            actor_worker_id,
            amount_cents,
            source_account_id=source_account_id,
            destination_account_id=destination_account_id,
            operation_id=operation_id,
        )
        run_id = f"LOOP-{uuid.uuid4().hex[:16].upper()}"
        tasks: list[dict[str, Any]] = []
        for step_no, worker_id in enumerate(operation["route"], start=1):
            context = self.memory.retrieve_memory(
                f"Review synthetic {operation_type} {operation['operation_id']} and its approval boundary.",
                worker_id,
                entity_id=source_account_id or destination_account_id,
            )
            tasks.append({
                "step_no": step_no,
                "worker_id": worker_id,
                "status": "queued",
                "attempts": 0,
                "max_retries": operation["max_retries"],
                "context_packet_id": context["packet_id"],
                "instructions": "Return provenance, confidence, and recommendation; do not settle or change ledger state.",
            })
        run = {
            "run_id": run_id,
            "owner_learner_id": owner_learner_id,
            "operation_id": operation["operation_id"],
            "workflow_id": operation["workflow_id"],
            "status": operation["status"],
            "loop": {"max_steps": operation["max_steps"], "max_retries": operation["max_retries"], "steps": tasks},
            "operation": operation,
            "approval_rule": operation["approval_policy"],
            "side_effects": [],
            "synthetic": True,
            "created_at": now(),
        }
        self.runs[run_id] = run
        self._persist(run)
        return self._public_run(run)

    @synchronized
    def approve(
        self,
        run_id: str,
        approver_worker_id: str,
        owner_learner_id: str | None = None,
        agent_token: str | None = None,
        agent_request_nonce: str | None = None,
    ) -> dict[str, Any]:
        self.kill_switch.check()
        if not self.circuit_breaker.allow():
            raise BankValidationError("synthetic orchestrator circuit breaker is open")
        self._authorize_agent_request(
            agent_token,
            agent_request_nonce,
            worker_id=approver_worker_id,
            capability="bank.operation.approve",
            owner_learner_id=owner_learner_id,
        )
        run = self.runs.get(run_id)
        if run is None:
            raise BankValidationError("unknown orchestrator loop")
        if run.get("owner_learner_id") is not None and run.get("owner_learner_id") != owner_learner_id:
            raise BankAuthorizationError("orchestrator loop belongs to another learner")
        result = self.memory.approve_operation(run["operation_id"], approver_worker_id)

        run["operation"] = result
        for task in run["loop"]["steps"]:
            if task["worker_id"] == approver_worker_id:
                task["status"] = "approved"
            elif result["status"] == "committed":
                task["status"] = "completed"
        run["status"] = result["status"]
        self._persist(run)
        return self._public_run(run)

    @synchronized
    def checkpoint(self, run_id: str) -> dict[str, Any]:
        run = self.runs.get(run_id)
        if run is None:
            raise BankValidationError("unknown orchestrator loop")
        sequence = len(self.memory.ledger)
        checkpoint = self.checkpoints.save(
            run_id,
            sequence,
            {"run": self._public_run(run), "ledger_sequence": sequence, "synthetic": True, "side_effects": []},
        )
        return {"checkpoint_id": checkpoint.checkpoint_id, "run_id": checkpoint.run_id, "sequence": checkpoint.sequence, "state_digest": checkpoint.state_digest, "synthetic": True, "side_effects": []}

    @synchronized
    def recover(self, checkpoint_id: str, run_id: str) -> dict[str, Any]:
        state = self.checkpoints.recover(checkpoint_id, run_id=run_id, minimum_sequence=0)
        return {"checkpoint_id": checkpoint_id, "run_id": run_id, "state": state, "verified": True, "ledger_mutation": False, "side_effects": []}

    @synchronized
    def reconcile(self) -> dict[str, Any]:
        opening = {item["account_id"]: int(item["opening_balance_cents"]) for item in self.memory.operations["virtual_accounts"]}
        return reconcile_ledger(self.memory.ledger, self.memory.balances, opening)

    @synchronized
    def resilience_snapshot(self) -> dict[str, Any]:
        return {"circuit_breaker": self.circuit_breaker.snapshot(), "kill_switch": self.kill_switch.snapshot(), "checkpoints": self.checkpoints.metrics(), "reconciliation": self.reconcile(), "side_effects": []}

    @synchronized
    def reject(self, run_id: str, worker_id: str, reason: str, owner_learner_id: str | None = None) -> dict[str, Any]:
        run = self.runs.get(run_id)
        if run is None:
            raise BankValidationError("unknown orchestrator loop")
        if run.get("owner_learner_id") is not None and run.get("owner_learner_id") != owner_learner_id:
            raise BankAuthorizationError("orchestrator loop belongs to another learner")
        result = self.memory.reject_operation(run["operation_id"], worker_id, reason)
        run["operation"] = result
        run["status"] = "rejected"
        for task in run["loop"]["steps"]:
            task["status"] = "cancelled"
        self._persist(run)
        return self._public_run(run)

    def _public_run(self, run: dict[str, Any]) -> dict[str, Any]:
        return {
            "run_id": run["run_id"],
            "operation_id": run["operation_id"],
            "workflow_id": run["workflow_id"],
            "status": run["status"],
            "loop": run["loop"],
            "operation": run["operation"],
            "approval_rule": run["approval_rule"],
            "side_effects": [],
            "synthetic": True,
        }

    def _persist(self, run: dict[str, Any]) -> None:
        if self.audit_path is None:
            return
        db = audit_connection(self.audit_path)
        try:
            public = self._public_run(run)
            db.execute(
                "INSERT OR REPLACE INTO orchestrator_runs(run_id, operation_id, workflow_id, status, created_at, result_json) VALUES (?, ?, ?, ?, ?, ?)",
                (run["run_id"], run["operation_id"], run["workflow_id"], run["status"], run["created_at"], json.dumps(public, sort_keys=True)),
            )
            for task in run["loop"]["steps"]:
                db.execute(
                    "INSERT OR REPLACE INTO orchestrator_loop_steps(run_id, step_no, worker_id, status, attempts, context_packet_id, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (run["run_id"], task["step_no"], task["worker_id"], task["status"], task["attempts"], task["context_packet_id"], now()),
                )
            db.commit()
        finally:
            db.close()


def demo() -> dict[str, Any]:
    """Run a complete receive + high-value transfer in one isolated memory."""
    orchestrator = BankOrchestrator()
    receive = orchestrator.plan("receive", "teller-north", 25_000, destination_account_id="ZB-ACCT-1001", operation_id="OP-DEMO-RECEIVE")
    receive = orchestrator.approve(receive["run_id"], "branch-manager-north")
    transfer = orchestrator.plan("transfer", "teller-north", 1_200_000, source_account_id="ZB-ACCT-1001", destination_account_id="ZB-ACCT-4001", operation_id="OP-DEMO-TRANSFER")
    transfer = orchestrator.approve(transfer["run_id"], "payments-analyst")
    transfer = orchestrator.approve(transfer["run_id"], "fraud-analyst")
    transfer = orchestrator.approve(transfer["run_id"], "compliance-officer")
    return {"receive": receive, "transfer": transfer, "final_snapshot": orchestrator.memory.snapshot()}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--demo", action="store_true", help="run receive and high-value transfer demo in isolated memory")
    p.add_argument("--operation", choices=("transfer", "receive", "withdraw"))
    p.add_argument("--actor", help="initiating synthetic employee worker_id")
    p.add_argument("--amount-cents", type=int)
    p.add_argument("--source-account")
    p.add_argument("--destination-account")
    p.add_argument("--operation-id")
    p.add_argument("--approve-by", action="append", default=[])
    p.add_argument("--reject-reason")
    p.add_argument("--audit-state", type=Path, default=None, help="optional local audit SQLite path")
    p.add_argument("--format", choices=("text", "json"), default="text")
    return p


def main() -> int:
    args = parser().parse_args()
    try:
        if args.demo:
            result = demo()
        else:
            if not args.operation or not args.actor or args.amount_cents is None:
                raise BankValidationError("--operation, --actor, and --amount-cents are required unless --demo is used")
            orchestrator = BankOrchestrator(audit_path=args.audit_state)
            result = orchestrator.plan(
                args.operation,
                args.actor,
                args.amount_cents,
                source_account_id=args.source_account,
                destination_account_id=args.destination_account,
                operation_id=args.operation_id,
            )
            for approver in args.approve_by:
                result = orchestrator.approve(result["run_id"], approver)
            if args.reject_reason:
                result = orchestrator.reject(result["run_id"], args.actor, args.reject_reason)
    except (BankValidationError, BankAuthorizationError, KeyError) as exc:
        print(f"[zodiac-bank-orchestrator] ERROR: {exc}", file=sys.stderr)
        return 1
    if args.format == "json" or args.demo:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"loop={result['run_id']} operation={result['operation_id']} status={result['status']}")
        print(f"workflow={result['workflow_id']} route={[task['worker_id'] for task in result['loop']['steps']]}")
        print(f"approval_rule={result['approval_rule']}")
        if result["operation"].get("receipt_id"):
            print(f"receipt={result['operation']['receipt_id']}")
        print("No external side effects; ledger is process-local virtual state.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
