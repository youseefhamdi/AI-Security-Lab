"""Synthetic runtime agentic supply-chain primitives for the Zodiac Bank lab.

Models the 2026 runtime supply-chain surface: poisoned model/GGUF artifacts,
registry squatting, and live MCP tool rug-pulls. The module verifies digests,
compares approved baselines, and detects near-name collisions; it never downloads,
installs, or executes an artifact.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class Artifact:
    name: str
    publisher: str
    digest: str
    source: str  # "approved-registry" | "untrusted-mirror" | "synthetic"
    approved: bool = False


@dataclass(frozen=True)
class ToolManifest:
    name: str
    description: str
    schema: dict[str, Any]
    version: str
    approved: bool = False


class SupplyChainViolation(PermissionError):
    pass


def artifact_digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def verify_artifact(artifact: Artifact, *, pinned_digest: str) -> dict[str, Any]:
    if artifact.digest != pinned_digest:
        return {"verdict": "block", "reason": "artifact digest does not match the pinned digest", "name": artifact.name, "publisher": artifact.publisher, "synthetic": True, "alert": "ZB-AI-016"}
    if artifact.source != "approved-registry":
        return {"verdict": "block", "reason": "artifact source is not an approved registry", "name": artifact.name, "source": artifact.source, "synthetic": True, "alert": "ZB-AI-016"}
    if not artifact.approved:
        return {"verdict": "block", "reason": "artifact is not approved for this workspace", "name": artifact.name, "synthetic": True, "alert": "ZB-AI-016"}
    return {"verdict": "allow", "reason": "artifact digest, source, and approval are valid", "name": artifact.name, "synthetic": True}


def manifest_fingerprint(manifest: ToolManifest) -> str:
    return hashlib.sha256(f"{manifest.name}|{manifest.description}|{manifest.schema}|{manifest.version}".encode("utf-8")).hexdigest()


def detect_rug_pull(before: ToolManifest, after: ToolManifest) -> dict[str, Any]:
    """Detect a live rug-pull: a tool's description or schema changed after approval."""
    if before.name != after.name:
        return {"verdict": "block", "reason": "tool identity changed", "before": before.name, "after": after.name, "synthetic": True, "alert": "ZB-AI-016"}
    if before.description != after.description:
        return {"verdict": "block", "reason": "tool description changed after approval (rug pull)", "name": before.name, "synthetic": True, "alert": "ZB-AI-016"}
    if before.schema != after.schema:
        return {"verdict": "block", "reason": "tool schema changed after approval (rug pull)", "name": before.name, "synthetic": True, "alert": "ZB-AI-016"}
    if manifest_fingerprint(before) != manifest_fingerprint(after):
        return {"verdict": "block", "reason": "tool manifest drifted after approval", "name": before.name, "synthetic": True, "alert": "ZB-AI-016"}
    return {"verdict": "allow", "reason": "tool manifest matches the approved baseline", "name": before.name, "synthetic": True}


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.casefold())


def detect_registry_squat(candidate: str, known_packages: Iterable[str]) -> dict[str, Any]:
    """Detect typosquat-style near-name collisions against known packages."""
    normalized_candidate = _normalize_name(candidate)
    collisions: list[str] = []
    for known in known_packages:
        normalized = _normalize_name(known)
        if not normalized or normalized == normalized_candidate:
            continue
        # Prefix/suffix similarity and one-character edits are the squatting signal.
        if normalized in normalized_candidate or normalized_candidate in normalized:
            collisions.append(known)
        elif len(normalized) == len(normalized_candidate) and sum(a != b for a, b in zip(normalized, normalized_candidate)) == 1:
            collisions.append(known)
    if collisions:
        return {"verdict": "block", "reason": "registry name is a near-collision with a known package", "candidate": candidate, "collisions": sorted(collisions), "synthetic": True, "alert": "ZB-AI-016"}
    return {"verdict": "allow", "reason": "registry name has no known near-collision", "candidate": candidate, "synthetic": True}


def snapshot() -> dict[str, Any]:
    return {"techniques": ["digest-drift", "rug-pull", "registry-squatting"], "synthetic": True, "side_effects": False}
