"""Synthetic deepfake and agentic financial-fraud primitives for the Zodiac Bank lab.

Models the 2026 fraud-industrialization signals: deepfake/biometric bypass,
agentic scam orchestration, and mule-network fan-out. All values are synthetic;
the module scores and flags, it never moves money or contacts a real party.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable


class FraudViolation(ValueError):
    pass


def deepfake_signal(*, liveness_consistency: bool, voiceprint_match: bool, device_reputation: str, biometric_score: float) -> dict[str, Any]:
    """Score a synthetic biometric/deepfake bypass signal."""
    risk = 0
    reasons: list[str] = []
    if not liveness_consistency:
        risk += 40
        reasons.append("liveness check failed")
    if not voiceprint_match:
        risk += 30
        reasons.append("voiceprint mismatch")
    if device_reputation == "new-unverified":
        risk += 20
        reasons.append("unverified device")
    if biometric_score < 0.6:
        risk += 10
        reasons.append("low biometric confidence")
    decision = "deny" if risk >= 70 else ("review" if risk >= 40 else "allow")
    return {"risk_score": risk, "decision": decision, "reasons": reasons, "synthetic": True, "alert": "ZB-AI-017" if decision != "allow" else None}


def agentic_scam_orchestration(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Detect coordinated multi-step scam orchestration from synthetic events."""
    rows = list(events)
    if not rows:
        raise FraudViolation("scam orchestration requires at least one event")
    pretext_count = sum(1 for row in rows if row.get("type") == "pretext")
    transfer_count = sum(1 for row in rows if row.get("type") == "transfer")
    mule_count = len({row.get("destination_account") for row in rows if row.get("type") == "transfer"})
    velocity = sum(1 for row in rows if row.get("velocity_flag"))
    risk = min(100, pretext_count * 20 + transfer_count * 10 + mule_count * 15 + velocity * 25)
    decision = "deny" if risk >= 70 else ("review" if risk >= 40 else "allow")
    return {"risk_score": risk, "decision": decision, "pretext_count": pretext_count, "transfer_count": transfer_count, "distinct_mules": mule_count, "velocity_flags": velocity, "synthetic": True, "alert": "ZB-AI-017" if decision != "allow" else None}


def mule_network_hub(edges: Iterable[dict[str, str]]) -> dict[str, Any]:
    """Detect fan-in/out hub accounts in a synthetic transaction graph."""
    out_degree: dict[str, int] = defaultdict(int)
    in_degree: dict[str, int] = defaultdict(int)
    edge_list: list[dict[str, str]] = []
    for edge in edges:
        source = edge.get("source_account_id", "")
        target = edge.get("destination_account_id", "")
        if not source or not target:
            raise FraudViolation("mule edge requires source and destination account ids")
        out_degree[source] += 1
        in_degree[target] += 1
        edge_list.append({"from": source, "to": target})
    hubs = sorted({account for account, degree in out_degree.items() if degree >= 3})
    return {"edges": edge_list, "hubs": hubs, "hub_detected": bool(hubs), "raw_transactions": False, "synthetic": True, "alert": "ZB-AI-017" if hubs else None}


def snapshot() -> dict[str, Any]:
    return {"techniques": ["deepfake-bypass", "agentic-scam-orchestration", "mule-network"], "synthetic": True, "side_effects": False}
