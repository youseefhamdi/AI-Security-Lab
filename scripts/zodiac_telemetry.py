"""Structured, privacy-safe telemetry for the synthetic Zodiac Bank range."""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Iterable

SCHEMA_VERSION = 1
MAX_EVENT_PAYLOAD_BYTES = 4096
MAX_STRING_LENGTH = 256


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()[:24]


def _safe_value(value: Any, *, key: str = "") -> Any:
    lowered = key.lower()
    if any(marker in lowered for marker in ("secret", "token", "password", "api_key", "raw", "prompt", "content")):
        return {"redacted": True, "hash": stable_hash(value)}
    if isinstance(value, dict):
        return {str(child_key): _safe_value(child_value, key=str(child_key)) for child_key, child_value in list(value.items())[:32]}
    if isinstance(value, list):
        return [_safe_value(item, key=key) for item in value[:32]]
    if isinstance(value, str):
        return value if len(value) <= MAX_STRING_LENGTH else value[:MAX_STRING_LENGTH] + "…[truncated]"
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return str(value)[:MAX_STRING_LENGTH]


def sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    safe = _safe_value(payload)
    encoded = json.dumps(safe, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if len(encoded.encode("utf-8")) <= MAX_EVENT_PAYLOAD_BYTES:
        return safe
    return {"payload_hash": stable_hash(payload), "truncated": True, "synthetic": True}


def make_event(
    event_type: str,
    *,
    actor_worker_id: str = "system",
    operation_id: str | None = None,
    learner_id: str | None = None,
    branch_id: str | None = None,
    trace_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    safe_payload = sanitize_payload(payload or {})
    event = {
        "schema_version": SCHEMA_VERSION,
        "event_id": f"EVT-{uuid.uuid4().hex[:20].upper()}",
        "trace_id": trace_id or f"TRACE-{uuid.uuid4().hex[:20].upper()}",
        "event_type": str(event_type),
        "actor_worker_id": str(actor_worker_id),
        "operation_id": operation_id,
        "learner_id": learner_id,
        "branch_id": branch_id,
        "timestamp": utc_now(),
        "payload": safe_payload,
        "classification": "synthetic-training-only",
        "synthetic": True,
        "side_effects": [],
    }
    event["event_hash"] = stable_hash(event)
    return event


class EventStore:
    """Thread-safe bounded event store with aggregate-only metrics."""

    def __init__(self, max_events: int = 4096) -> None:
        self.max_events = max(64, int(max_events))
        self._events: list[dict[str, Any]] = []
        self._lock = threading.RLock()

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(event, dict) or event.get("synthetic") is not True:
            raise ValueError("only synthetic structured events can be stored")
        with self._lock:
            if len(self._events) >= self.max_events:
                raise OverflowError("bounded telemetry capacity reached")
            copy = deepcopy(event)
            self._events.append(copy)
            return deepcopy(copy)

    def events(self, *, trace_id: str | None = None, event_type: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            return [
                deepcopy(event)
                for event in self._events
                if (trace_id is None or event.get("trace_id") == trace_id)
                and (event_type is None or event.get("event_type") == event_type)
            ]

    def metrics(self) -> dict[str, Any]:
        with self._lock:
            by_type: dict[str, int] = {}
            by_branch: dict[str, int] = {}
            for event in self._events:
                event_type = str(event.get("event_type", "unknown"))
                by_type[event_type] = by_type.get(event_type, 0) + 1
                branch = str(event.get("branch_id") or "unscoped")
                by_branch[branch] = by_branch.get(branch, 0) + 1
            return {"events": len(self._events), "by_type": dict(sorted(by_type.items())), "by_branch": dict(sorted(by_branch.items())), "raw_content": False, "external_egress": False}


class AlertCorrelator:
    """Convert structured synthetic events into deterministic alert records."""

    def correlate(self, events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        alerts: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for event in events:
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            event_type = event.get("event_type")
            score = payload.get("risk_score")
            if event_type == "fraud_assessment" and isinstance(score, (int, float)) and float(score) >= 60:
                alert_id = (str(event.get("trace_id")), "ZB-FRAUD-001")
                if alert_id not in seen:
                    seen.add(alert_id)
                    alerts.append(self._alert(event, "ZB-FRAUD-001", "Synthetic transaction risk exceeded review threshold", "high"))
            if event_type == "agent_request" and payload.get("circuit_breaker") is True:
                alert_id = (str(event.get("trace_id")), "ZB-AI-003")
                if alert_id not in seen:
                    seen.add(alert_id)
                    alerts.append(self._alert(event, "ZB-AI-003", "Synthetic agent volume or fan-out exceeded baseline", "medium"))
            if payload.get("identity_mismatch") is True:
                alert_id = (str(event.get("trace_id")), "ZB-AI-004")
                if alert_id not in seen:
                    seen.add(alert_id)
                    alerts.append(self._alert(event, "ZB-AI-004", "Synthetic identity or approval context mismatch", "critical"))
        return alerts

    @staticmethod
    def _alert(event: dict[str, Any], rule_id: str, title: str, severity: str) -> dict[str, Any]:
        return {
            "alert_id": f"ALERT-{uuid.uuid4().hex[:16].upper()}",
            "rule_id": rule_id,
            "title": title,
            "severity": severity,
            "trace_id": event.get("trace_id"),
            "event_id": event.get("event_id"),
            "operation_id": event.get("operation_id"),
            "status": "open",
            "synthetic": True,
            "side_effects": [],
        }
