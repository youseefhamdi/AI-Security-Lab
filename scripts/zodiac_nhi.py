"""Synthetic non-human identity (NHI) lifecycle primitives for the Zodiac Bank lab.

Models the 2026 NHI governance gap: machine credentials that never expire,
never rotate, lack an owner, outlive the human session that authorized them, or
are relayed through delegation chains. All values are synthetic and the module
never issues real credentials or contacts an identity provider.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Iterable


class NHIViolation(PermissionError):
    """Raised when an NHI lifecycle policy is violated."""


@dataclass
class NHICredential:
    credential_id: str
    owner: str
    kind: str  # "oauth-token" | "api-key" | "service-account" | "agent-delegation"
    capabilities: set[str]
    issued_at: int
    expires_at: int
    rotation_due_at: int
    revoked: bool = False
    synthetic: bool = True


def _digest(credential: NHICredential) -> str:
    return hashlib.sha256(credential.credential_id.encode("utf-8")).hexdigest()[:12]


class NHIInventory:
    """Bounded synthetic credential inventory with lifecycle checks."""

    def __init__(self) -> None:
        self._credentials: dict[str, NHICredential] = {}

    def add(self, credential: NHICredential) -> None:
        if credential.credential_id in self._credentials:
            raise NHIViolation("duplicate NHI credential id")
        self._credentials[credential.credential_id] = credential

    def rotate(self, credential_id: str, *, now: int, ttl_seconds: int = 300, capabilities: Iterable[str] | None = None) -> dict[str, Any]:
        credential = self._credentials.get(credential_id)
        if credential is None:
            raise NHIViolation("unknown NHI credential")
        credential.revoked = True
        new_id = f"{credential.credential_id}-rot-{_digest(credential)}"
        rotated = NHICredential(
            credential_id=new_id,
            owner=credential.owner,
            kind=credential.kind,
            capabilities=set(capabilities) if capabilities is not None else set(credential.capabilities),
            issued_at=now,
            expires_at=now + ttl_seconds,
            rotation_due_at=now + ttl_seconds // 2,
        )
        self._credentials[new_id] = rotated
        return {"revoked": credential.credential_id, "issued": new_id, "expires_at": rotated.expires_at, "synthetic": True}

    def check_still_acting(self, credential_id: str, *, now: int) -> dict[str, Any]:
        """A revoked or expired credential must not continue acting."""
        credential = self._credentials.get(credential_id)
        if credential is None:
            raise NHIViolation("unknown NHI credential")
        if credential.revoked:
            return {"verdict": "block", "reason": "credential is revoked", "credential_id": credential_id, "synthetic": True}
        if credential.expires_at <= now:
            return {"verdict": "block", "reason": "credential is expired", "credential_id": credential_id, "synthetic": True}
        return {"verdict": "allow", "reason": "credential is valid", "credential_id": credential_id, "synthetic": True}

    def lifecycle_scan(self, *, now: int) -> dict[str, Any]:
        expired: list[str] = []
        due_rotation: list[str] = []
        orphaned: list[str] = []
        long_lived: list[str] = []
        for credential in self._credentials.values():
            if credential.revoked:
                continue
            if credential.expires_at <= now:
                expired.append(credential.credential_id)
            if credential.rotation_due_at <= now:
                due_rotation.append(credential.credential_id)
            if not credential.owner:
                orphaned.append(credential.credential_id)
            if credential.expires_at - credential.issued_at > 86400 * 365:
                long_lived.append(credential.credential_id)
        return {
            "total": len(self._credentials),
            "expired": sorted(expired),
            "due_rotation": sorted(due_rotation),
            "orphaned": sorted(orphaned),
            "long_lived": sorted(long_lived),
            "synthetic": True,
            "raw_secrets": False,
        }


def delegation_chain_relay(chain: Iterable[dict[str, Any]], *, max_depth: int = 4) -> dict[str, Any]:
    """Detect NHI credential relay through a delegation chain (reuses control-plane semantics)."""
    hops = list(chain)
    if not hops or len(hops) > max_depth:
        return {"verdict": "block", "reason": "delegation chain is empty or exceeds depth", "synthetic": True}
    seen = set()
    previous: set[str] | None = None
    for index, hop in enumerate(hops):
        subject = str(hop.get("subject", ""))
        caps = set(str(value) for value in hop.get("capabilities", []))
        if subject in seen:
            return {"verdict": "block", "reason": "credential relay: subject reused across hops", "hop": index, "synthetic": True}
        seen.add(subject)
        if previous is not None and not caps.issubset(previous):
            return {"verdict": "block", "reason": "credential relay: capability scope widened", "hop": index, "synthetic": True}
        previous = caps
    return {"verdict": "allow", "reason": "delegation chain narrows monotonically", "hops": len(hops), "synthetic": True}
