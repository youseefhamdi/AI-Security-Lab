"""Shared dynamic-profile helpers for the synthetic Zodiac Bank services."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_profiles(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema_version") != 1:
        raise RuntimeError("unsupported bank profile schema")
    profiles = document.get("profiles")
    if not isinstance(profiles, list) or len(profiles) < 2:
        raise RuntimeError("bank profiles must contain at least two profiles")
    ids = [profile.get("profile_id") for profile in profiles]
    if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
        raise RuntimeError("bank profile IDs must be unique strings")
    levels = [profile.get("level") for profile in profiles]
    if levels != list(range(1, len(profiles) + 1)):
        raise RuntimeError("bank profile levels must be contiguous and ordered")
    stage_ids = [profile.get("stage_id") for profile in profiles[:-1]]
    if any(not isinstance(value, str) or not value for value in stage_ids):
        raise RuntimeError("active bank profiles must map to a stage")
    if profiles[-1].get("stage_id") is not None:
        raise RuntimeError("final bank profile must be post-course review")
    for profile in profiles:
        for key in ("data_domains", "branch_scope", "active_services", "controls"):
            if not isinstance(profile.get(key), list) or not profile[key]:
                raise RuntimeError(f"profile {profile.get('profile_id')} has invalid {key}")
        policy = profile.get("agent_policy")
        if (
            not isinstance(policy, dict)
            or policy.get("external_egress") is not False
            or not isinstance(policy.get("max_parallel_scenarios"), int)
            or not 1 <= policy["max_parallel_scenarios"] <= 2
        ):
            raise RuntimeError(f"profile {profile.get('profile_id')} has an invalid parallel-scenario budget")
    document["profiles"] = profiles
    return document


def profile_by_id(document: dict[str, Any], profile_id: str) -> dict[str, Any]:
    for profile in document["profiles"]:
        if profile["profile_id"] == profile_id:
            return profile
    raise RuntimeError(f"unknown bank profile: {profile_id}")


def profile_for_stage(document: dict[str, Any], stage_id: str | None) -> dict[str, Any]:
    for profile in document["profiles"]:
        if profile.get("stage_id") == stage_id:
            return profile
    if stage_id is None:
        return document["profiles"][-1]
    raise RuntimeError(f"no bank profile for stage: {stage_id}")


def public_profile(profile: dict[str, Any], *, promotion_count: int = 0, updated_at: str | None = None) -> dict[str, Any]:
    """Return operational posture, excluding secrets and future stage answers."""
    result = {key: value for key, value in profile.items() if key != "promotion"}
    result["promotion_count"] = promotion_count
    if updated_at is not None:
        result["updated_at"] = updated_at
    result["safety"] = {
        "classification": "synthetic-training-only",
        "network": "localhost-only",
        "real_transactions": False,
        "external_egress": False,
    }
    return result
