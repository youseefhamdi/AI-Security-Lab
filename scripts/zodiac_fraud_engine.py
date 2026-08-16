"""Deterministic fraud intelligence for the synthetic Zodiac Bank ledger."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any, Iterable

MAX_RISK_SCORE = 100
REVIEW_THRESHOLD = 45
DENY_THRESHOLD = 90


def _signal(name: str, weight: int, detail: str) -> dict[str, Any]:
    return {"name": name, "weight": int(weight), "detail": detail, "synthetic": True}


def build_mule_network(transactions: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Build an aggregate account graph without retaining raw transaction text."""
    adjacency: dict[str, set[str]] = defaultdict(set)
    counts: dict[str, int] = defaultdict(int)
    for item in transactions:
        source = str(item.get("source_account_id") or "")
        destination = str(item.get("destination_account_id") or "")
        if source and destination and source != destination:
            adjacency[source].add(destination)
            adjacency[destination].add(source)
            counts[source] += 1
            counts[destination] += 1
    nodes = sorted(set(adjacency) | set(counts))
    edges = sorted({tuple(sorted((source, destination))) for source, targets in adjacency.items() for destination in targets})
    hubs = sorted(({"account_id": account, "degree": len(adjacency.get(account, set())), "transaction_count": counts[account]} for account in nodes), key=lambda item: (-item["degree"], item["account_id"]))
    return {"nodes": nodes, "edges": [{"from": source, "to": destination} for source, destination in edges], "hubs": hubs[:16], "synthetic": True, "raw_transactions": False}


def assess_transaction(
    *,
    operation_type: str,
    amount_cents: int,
    source_account: dict[str, Any] | None,
    destination_account: dict[str, Any] | None,
    source_customer: dict[str, Any] | None,
    destination_customer: dict[str, Any] | None,
    recent_transactions: Iterable[dict[str, Any]] = (),
    device_trusted: bool = True,
    session_age_seconds: int = 0,
    beneficiary_age_days: int = 365,
) -> dict[str, Any]:
    """Return an explainable risk decision over synthetic metadata only."""
    signals: list[dict[str, Any]] = []
    amount = max(0, int(amount_cents))
    score = 0
    if amount >= 1_000_000:
        score += 25
        signals.append(_signal("high_value", 25, "amount crossed the synthetic high-value threshold"))
    if amount >= 5_000_000:
        score += 25
        signals.append(_signal("very_high_value", 25, "amount entered the elevated synthetic review band"))
    customers = [item for item in (source_customer, destination_customer) if item]
    if any(item.get("risk_rating") == "high" for item in customers):
        score += 25
        signals.append(_signal("high_risk_customer", 25, "one synthetic customer is marked high risk"))
    if any(item.get("kyc_status") != "verified" for item in customers):
        score += 15
        signals.append(_signal("kyc_review", 15, "customer verification is not in the verified state"))
    if destination_account and destination_account.get("status") == "monitored":
        score += 20
        signals.append(_signal("monitored_beneficiary", 20, "destination synthetic account is monitored"))
    if not device_trusted:
        score += 20
        signals.append(_signal("untrusted_device", 20, "synthetic device signal is not trusted"))
    if session_age_seconds > 86_400:
        score += 10
        signals.append(_signal("stale_session", 10, "synthetic session exceeded the freshness window"))
    if beneficiary_age_days < 7:
        score += 20
        signals.append(_signal("new_beneficiary", 20, "synthetic beneficiary was created recently"))
    recent = list(recent_transactions)
    same_source = [item for item in recent if item.get("source_account_id") == (source_account or {}).get("account_id")]
    if len(same_source) >= 3:
        score += 20
        signals.append(_signal("velocity", 20, "synthetic source velocity exceeded the baseline"))
    if len({item.get("destination_account_id") for item in same_source if item.get("destination_account_id")}) >= 3:
        score += 15
        signals.append(_signal("fan_out", 15, "synthetic source fan-out exceeded the baseline"))
    if operation_type == "withdraw" and amount >= 500_000:
        score += 15
        signals.append(_signal("cash_intensity", 15, "large synthetic cash withdrawal"))
    score = min(MAX_RISK_SCORE, score)
    decision = "deny" if score >= DENY_THRESHOLD else ("review" if score >= REVIEW_THRESHOLD else "allow")
    return {
        "decision": decision,
        "risk_score": score,
        "signals": signals,
        "policy": {"review_threshold": REVIEW_THRESHOLD, "deny_threshold": DENY_THRESHOLD},
        "customer_data": "synthetic-metadata-only",
        "network": build_mule_network(recent),
        "explanation": [item["detail"] for item in signals],
        "synthetic": True,
        "side_effects": [],
    }


def public_assessment(assessment: dict[str, Any]) -> dict[str, Any]:
    """Return an instructor-safe assessment with no raw account/customer data."""
    result = deepcopy(assessment)
    result.pop("network", None)
    result["signal_count"] = len(result.get("signals", []))
    result["signals"] = [{"name": item["name"], "weight": item["weight"]} for item in result.get("signals", [])]
    result["explanation"] = list(result.get("explanation", []))[:8]
    return result
