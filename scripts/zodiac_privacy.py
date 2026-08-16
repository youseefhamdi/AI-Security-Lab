"""Privacy and data-governance controls for synthetic Zodiac Bank records."""

from __future__ import annotations

import hashlib
import threading
import time
from copy import deepcopy
from typing import Any, Iterable

CLASS_ORDER = {"public": 0, "internal": 1, "sensitive": 2, "restricted": 3}
DEFAULT_RETENTION_SECONDS = 7 * 24 * 60 * 60


def digest(value: Any) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:24]


class PrivacyDenied(PermissionError):
    """Raised when a synthetic record access violates policy."""


class PrivacyGuard:
    """Enforce branch, purpose, role, field, and retention boundaries."""

    def __init__(self, retention_seconds: int = DEFAULT_RETENTION_SECONDS) -> None:
        self.retention_seconds = max(60, int(retention_seconds))
        self._audits: list[dict[str, Any]] = []
        self._lock = threading.RLock()

    def authorize(
        self,
        *,
        actor_worker_id: str,
        actor_role: str,
        actor_branch_id: str | None,
        record: dict[str, Any],
        purpose: str,
        requested_fields: Iterable[str] = (),
        now: float | None = None,
    ) -> dict[str, Any]:
        record_branch = record.get("branch_id") or record.get("home_branch_id")
        classification = str(record.get("classification", "internal"))
        if classification not in CLASS_ORDER:
            raise PrivacyDenied("unknown data classification")
        if not purpose or len(purpose) > 128:
            raise PrivacyDenied("bounded purpose is required")
        if actor_role in {"teller", "branch_manager"} and record_branch and actor_branch_id != record_branch:
            self._audit(actor_worker_id, record, purpose, "deny", "branch_scope", now=now)
            raise PrivacyDenied("branch worker cannot access another branch record")
        if classification == "restricted" and actor_role not in {"compliance_officer", "fraud_analyst", "aml_investigator", "siem_analyst"}:
            self._audit(actor_worker_id, record, purpose, "deny", "restricted_role", now=now)
            raise PrivacyDenied("role cannot access restricted synthetic data")
        fields = [str(field) for field in requested_fields]
        if len(fields) > 32:
            raise PrivacyDenied("field request is too broad")
        self._audit(actor_worker_id, record, purpose, "allow", "policy", fields=fields, now=now)
        return {"allowed": True, "classification": classification, "purpose": purpose, "fields": fields, "scope": record_branch or "central", "raw_content": False, "synthetic": True}

    def project(self, record: dict[str, Any], decision: dict[str, Any], *, requested_fields: Iterable[str] = ()) -> dict[str, Any]:
        fields = set(str(field) for field in requested_fields)
        if not decision.get("allowed"):
            return {}
        result: dict[str, Any] = {}
        for key, value in record.items():
            if fields and key not in fields:
                continue
            if key in {"name", "email", "phone", "address", "raw_content", "prompt"}:
                result[key] = {"redacted": True, "hash": digest(value)}
            else:
                result[key] = deepcopy(value)
        result["privacy"] = {"classification": decision.get("classification"), "purpose": decision.get("purpose"), "redacted": True, "synthetic": True}
        return result

    def purge_expired(self, records: Iterable[dict[str, Any]], *, now: float | None = None) -> tuple[list[dict[str, Any]], int]:
        current = time.time() if now is None else float(now)
        retained: list[dict[str, Any]] = []
        removed = 0
        for record in records:
            created = float(record.get("created_epoch", current))
            if current - created > self.retention_seconds:
                removed += 1
            else:
                retained.append(deepcopy(record))
        return retained, removed

    def audit(self) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(self._audits)

    def metrics(self) -> dict[str, Any]:
        with self._lock:
            return {"access_events": len(self._audits), "denied": sum(item["decision"] == "deny" for item in self._audits), "raw_content": False, "synthetic": True}

    def _audit(self, actor: str, record: dict[str, Any], purpose: str, decision: str, reason: str, *, fields: list[str] | None = None, now: float | None = None) -> None:
        with self._lock:
            self._audits.append({"actor_hash": digest(actor), "record_hash": digest(record.get("entity_id") or record.get("memory_id") or "record"), "purpose": purpose, "decision": decision, "reason": reason, "field_count": len(fields or []), "timestamp_epoch": time.time() if now is None else float(now), "synthetic": True, "raw_content": False})
            if len(self._audits) > 2048:
                del self._audits[: len(self._audits) - 2048]
