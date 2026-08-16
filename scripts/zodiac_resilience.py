"""Deterministic resilience and recovery primitives for Zodiac Bank workflows."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Any


def state_hash(state: Any) -> str:
    return hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


class RecoveryViolation(RuntimeError):
    """Raised when checkpoint or replay integrity fails."""


@dataclass(frozen=True)
class Checkpoint:
    checkpoint_id: str
    run_id: str
    sequence: int
    state_digest: str
    state: dict[str, Any]
    created_epoch: float
    synthetic: bool = True


class CheckpointStore:
    def __init__(self, max_checkpoints: int = 512) -> None:
        self.max_checkpoints = max(32, int(max_checkpoints))
        self._items: dict[str, Checkpoint] = {}
        self._lock = threading.RLock()

    def save(self, run_id: str, sequence: int, state: dict[str, Any], *, checkpoint_id: str | None = None, now: float | None = None) -> Checkpoint:
        if not run_id or int(sequence) < 0 or not isinstance(state, dict):
            raise RecoveryViolation("checkpoint requires a run ID, non-negative sequence, and object state")
        with self._lock:
            if len(self._items) >= self.max_checkpoints:
                raise RecoveryViolation("checkpoint capacity reached")
            identifier = checkpoint_id or f"CP-{run_id}-{int(sequence)}"
            checkpoint = Checkpoint(identifier, run_id, int(sequence), state_hash(state), deepcopy(state), time.time() if now is None else float(now))
            self._items[identifier] = checkpoint
            return deepcopy(checkpoint)

    def load(self, checkpoint_id: str) -> Checkpoint:
        with self._lock:
            checkpoint = self._items.get(checkpoint_id)
            if checkpoint is None:
                raise RecoveryViolation("unknown checkpoint")
            if state_hash(checkpoint.state) != checkpoint.state_digest:
                raise RecoveryViolation("checkpoint digest mismatch")
            return deepcopy(checkpoint)

    def recover(self, checkpoint_id: str, *, run_id: str, minimum_sequence: int = 0) -> dict[str, Any]:
        checkpoint = self.load(checkpoint_id)
        if checkpoint.run_id != run_id:
            raise RecoveryViolation("checkpoint belongs to another run")
        if checkpoint.sequence < int(minimum_sequence):
            raise RecoveryViolation("recovery would move the run backward")
        return deepcopy(checkpoint.state)

    def metrics(self) -> dict[str, Any]:
        with self._lock:
            return {"checkpoints": len(self._items), "synthetic": True, "raw_secrets": False, "external_egress": False}


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, reset_after_seconds: int = 30) -> None:
        self.failure_threshold = max(1, int(failure_threshold))
        self.reset_after_seconds = max(1, int(reset_after_seconds))
        self.failures = 0
        self.opened_epoch: float | None = None
        self._lock = threading.RLock()

    @property
    def state(self) -> str:
        with self._lock:
            if self.opened_epoch is None:
                return "closed"
            if time.time() - self.opened_epoch >= self.reset_after_seconds:
                self.opened_epoch = None
                self.failures = 0
                return "half-open"
            return "open"

    def allow(self) -> bool:
        return self.state != "open"

    def record_success(self) -> None:
        with self._lock:
            self.failures = 0
            self.opened_epoch = None

    def record_failure(self) -> str:
        with self._lock:
            self.failures += 1
            if self.failures >= self.failure_threshold:
                self.opened_epoch = time.time()
            return self.state

    def snapshot(self) -> dict[str, Any]:
        return {"state": self.state, "failures": self.failures, "failure_threshold": self.failure_threshold, "side_effects": []}


class KillSwitch:
    def __init__(self) -> None:
        self._engaged = False
        self._reason = ""
        self._lock = threading.Lock()

    def engage(self, reason: str) -> None:
        with self._lock:
            self._engaged = True
            self._reason = str(reason)[:256]

    def release(self) -> None:
        with self._lock:
            self._engaged = False
            self._reason = ""

    def check(self) -> None:
        with self._lock:
            if self._engaged:
                raise RecoveryViolation(f"kill switch engaged: {self._reason}")

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"engaged": self._engaged, "reason": self._reason, "synthetic": True}


def reconcile_ledger(ledger: list[dict[str, Any]], balances: dict[str, int], opening_balances: dict[str, int]) -> dict[str, Any]:
    """Verify virtual double-entry totals without exposing raw customer data."""
    calculated = dict(opening_balances)
    debit = 0
    credit = 0
    for event in ledger:
        entries = event.get("entries") if isinstance(event.get("entries"), list) else []
        total_debit = sum(int(item.get("amount_cents", 0)) for item in entries if item.get("direction") == "debit")
        total_credit = sum(int(item.get("amount_cents", 0)) for item in entries if item.get("direction") == "credit")
        if total_debit != total_credit:
            return {"balanced": False, "reason": "event_entry_imbalance", "checked_events": len(ledger), "synthetic": True}
        debit += total_debit
        credit += total_credit
        for item in entries:
            account_id = item.get("account_id")
            if account_id in calculated:
                amount = int(item.get("amount_cents", 0))
                calculated[account_id] += amount if item.get("direction") == "credit" else -amount
    balanced = calculated == {key: int(value) for key, value in balances.items()}
    return {"balanced": balanced, "reason": "ok" if balanced else "balance_divergence", "checked_events": len(ledger), "debit_cents": debit, "credit_cents": credit, "synthetic": True, "raw_customer_data": False}
