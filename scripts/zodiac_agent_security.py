"""Local-only security primitives for Zodiac Bank agent and MCP boundaries.

This module is deliberately independent of FastAPI and network clients so the
same contracts can be used by the MCP wrapper, A2A services, the orchestrator,
and offline regression tests. It signs short-lived HMAC tokens; it is not a
replacement for a production identity provider.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from copy import deepcopy
from typing import Any, Iterable

TOKEN_VERSION = "zbt1"
DEFAULT_AUDIENCE = "zodiac-bank"
MIN_SIGNING_KEY_BYTES = 32
MAX_TOKEN_BYTES = 8192
MAX_DELEGATION_DEPTH = 4
MAX_CLOCK_SKEW_SECONDS = 15


class AgentSecurityError(PermissionError):
    """Raised when a synthetic agent request violates an identity policy."""


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _key(signing_key: bytes | str) -> bytes:
    key = signing_key.encode("utf-8") if isinstance(signing_key, str) else bytes(signing_key)
    if len(key) < MIN_SIGNING_KEY_BYTES:
        raise AgentSecurityError(f"agent signing key must contain at least {MIN_SIGNING_KEY_BYTES} bytes")
    return key


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _signature(signing_key: bytes | str, body: str) -> str:
    return _b64(hmac.new(_key(signing_key), body.encode("ascii"), hashlib.sha256).digest())


def manifest_digest(tools: Iterable[dict[str, Any]]) -> str:
    """Return a stable digest over security-relevant tool metadata."""
    normalized = []
    for tool in tools:
        normalized.append({
            "name": str(tool.get("name", "")),
            "description": str(tool.get("description", "")),
            "inputSchema": deepcopy(tool.get("inputSchema", {})),
        })
    normalized.sort(key=lambda item: item["name"])
    return hashlib.sha256(_canonical(normalized)).hexdigest()


def issue_agent_token(
    signing_key: bytes | str,
    *,
    subject: str,
    audience: str,
    capabilities: Iterable[str],
    branch_id: str | None = None,
    learner_id: str | None = None,
    parent_jti: str | None = None,
    manifest: str | None = None,
    ttl_seconds: int = 300,
    now: int | None = None,
    jti: str | None = None,
) -> str:
    """Issue a bounded synthetic worker/delegation token.

    The caller must be an already trusted local control plane. No HTTP endpoint
    in this module issues tokens automatically.
    """
    if not subject or not audience:
        raise AgentSecurityError("subject and audience are required")
    if ttl_seconds < 1 or ttl_seconds > 900:
        raise AgentSecurityError("token TTL must be between 1 and 900 seconds")
    capabilities = sorted({str(value) for value in capabilities if str(value)})
    if not capabilities or len(capabilities) > 32:
        raise AgentSecurityError("a token must contain 1 to 32 capabilities")
    issued = int(time.time() if now is None else now)
    payload: dict[str, Any] = {
        "v": TOKEN_VERSION,
        "sub": str(subject),
        "aud": str(audience),
        "cap": capabilities,
        "iat": issued,
        "exp": issued + int(ttl_seconds),
        "jti": str(jti or secrets.token_urlsafe(18)),
        "depth": 0 if parent_jti is None else 1,
    }
    if branch_id is not None:
        payload["branch"] = str(branch_id)
    if learner_id is not None:
        payload["learner"] = str(learner_id)
    if parent_jti is not None:
        payload["parent"] = str(parent_jti)
    if manifest is not None:
        payload["manifest"] = str(manifest)
    if payload["depth"] > MAX_DELEGATION_DEPTH:
        raise AgentSecurityError("delegation chain exceeds the maximum depth")
    encoded = _b64(_canonical(payload))
    body = f"{TOKEN_VERSION}.{encoded}"
    return f"{body}.{_signature(signing_key, body)}"


def verify_agent_token(
    token: str,
    signing_key: bytes | str,
    *,
    audience: str,
    required_capability: str | None = None,
    subject: str | None = None,
    branch_id: str | None = None,
    learner_id: str | None = None,
    manifest: str | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    """Verify signature, time, audience, identity, delegation depth, and scope."""
    if not isinstance(token, str) or len(token.encode("utf-8")) > MAX_TOKEN_BYTES:
        raise AgentSecurityError("malformed agent token")
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != TOKEN_VERSION:
        raise AgentSecurityError("unsupported agent token")
    body = f"{parts[0]}.{parts[1]}"
    expected = _signature(signing_key, body)
    if not hmac.compare_digest(expected, parts[2]):
        raise AgentSecurityError("invalid agent token signature")
    try:
        payload = json.loads(_unb64(parts[1]).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AgentSecurityError("invalid agent token payload") from exc
    if not isinstance(payload, dict) or payload.get("v") != TOKEN_VERSION:
        raise AgentSecurityError("invalid agent token claims")
    current = int(time.time() if now is None else now)
    try:
        issued = int(payload["iat"])
        expires = int(payload["exp"])
        depth = int(payload.get("depth", 0))
    except (KeyError, TypeError, ValueError) as exc:
        raise AgentSecurityError("invalid agent token time claims") from exc
    if issued > current + MAX_CLOCK_SKEW_SECONDS or expires < current - MAX_CLOCK_SKEW_SECONDS or expires <= issued:
        raise AgentSecurityError("expired or not-yet-valid agent token")
    if depth < 0 or depth > MAX_DELEGATION_DEPTH:
        raise AgentSecurityError("invalid delegation depth")
    if payload.get("aud") != audience:
        raise AgentSecurityError("agent token audience mismatch")
    if subject is not None and payload.get("sub") != subject:
        raise AgentSecurityError("agent token subject mismatch")
    if branch_id is not None and payload.get("branch") != branch_id:
        raise AgentSecurityError("agent token branch scope mismatch")
    if learner_id is not None and payload.get("learner") != learner_id:
        raise AgentSecurityError("agent token learner scope mismatch")
    if manifest is not None and payload.get("manifest") != manifest:
        raise AgentSecurityError("agent token is bound to a different manifest")
    capabilities = payload.get("cap")
    if not isinstance(capabilities, list) or any(not isinstance(item, str) for item in capabilities):
        raise AgentSecurityError("invalid agent capabilities")
    if required_capability is not None and not any(
        item == required_capability or (item.endswith(".*") and required_capability.startswith(item[:-1]))
        for item in capabilities
    ):
        raise AgentSecurityError("agent capability is not authorized")
    return payload


class ReplayGuard:
    """Bounded request-nonce replay guard for one service process."""

    def __init__(self, max_entries: int = 4096) -> None:
        self.max_entries = max(64, int(max_entries))
        self._seen: dict[str, int] = {}
        self._lock = threading.Lock()

    def accept(self, nonce: str, expires_at: int, *, now: int | None = None) -> None:
        if not isinstance(nonce, str) or not nonce or len(nonce) > 128:
            raise AgentSecurityError("request nonce is required")
        current = int(time.time() if now is None else now)
        with self._lock:
            self._seen = {key: expiry for key, expiry in self._seen.items() if expiry >= current}
            if nonce in self._seen:
                raise AgentSecurityError("request nonce replay rejected")
            if len(self._seen) >= self.max_entries:
                raise AgentSecurityError("bounded replay cache is full")
            self._seen[nonce] = min(int(expires_at), current + 900)


def verify_request(
    token: str,
    signing_key: bytes | str,
    replay_guard: ReplayGuard,
    *,
    request_nonce: str,
    audience: str,
    required_capability: str,
    subject: str | None = None,
    branch_id: str | None = None,
    learner_id: str | None = None,
    manifest: str | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    claims = verify_agent_token(
        token,
        signing_key,
        audience=audience,
        required_capability=required_capability,
        subject=subject,
        branch_id=branch_id,
        learner_id=learner_id,
        manifest=manifest,
        now=now,
    )
    replay_guard.accept(request_nonce, int(claims["exp"]), now=now)
    return claims


def delegate_token(
    signing_key: bytes | str,
    parent_claims: dict[str, Any],
    *,
    subject: str,
    audience: str,
    capabilities: Iterable[str],
    ttl_seconds: int = 120,
    manifest: str | None = None,
) -> str:
    """Create a narrower child token from verified parent claims."""
    parent_depth = int(parent_claims.get("depth", 0))
    parent_caps = set(parent_claims.get("cap", []))
    requested = set(str(value) for value in capabilities)
    if not requested or not requested.issubset(parent_caps) and "delegation.*" not in parent_caps:
        raise AgentSecurityError("delegated capabilities exceed parent authority")
    return issue_agent_token(
        signing_key,
        subject=subject,
        audience=audience,
        capabilities=requested,
        branch_id=parent_claims.get("branch"),
        learner_id=parent_claims.get("learner"),
        parent_jti=str(parent_claims.get("jti")),
        manifest=manifest,
        ttl_seconds=ttl_seconds,
    )


def validate_tool_call(
    tools: Iterable[dict[str, Any]],
    tool_name: str,
    arguments: Any,
    *,
    allowed_tools: set[str],
) -> dict[str, Any]:
    """Validate an MCP call against the pinned tool schema without executing it."""
    if tool_name not in allowed_tools:
        raise AgentSecurityError("tool is not in the caller allowlist")
    if not isinstance(arguments, dict) or len(arguments) > 16:
        raise AgentSecurityError("tool arguments must be a bounded object")
    tool = next((item for item in tools if item.get("name") == tool_name), None)
    if tool is None:
        raise AgentSecurityError("tool is not in the pinned manifest")
    schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {}
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = schema.get("required") if isinstance(schema.get("required"), list) else []
    if set(arguments) - set(properties):
        raise AgentSecurityError("tool arguments contain undeclared fields")
    if any(field not in arguments for field in required):
        raise AgentSecurityError("tool arguments are missing a required field")
    for key, value in arguments.items():
        expected = properties[key].get("type") if isinstance(properties[key], dict) else None
        if expected == "string" and not isinstance(value, str):
            raise AgentSecurityError(f"tool argument {key} must be a string")
        if expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            raise AgentSecurityError(f"tool argument {key} must be an integer")
        if isinstance(value, str) and len(value) > 2048:
            raise AgentSecurityError(f"tool argument {key} exceeds the size limit")
    return {"tool": tool_name, "arguments": deepcopy(arguments), "manifest": manifest_digest(tools), "side_effects": []}


def issue_demo_token(signing_key: bytes | str, *, subject: str, audience: str, capabilities: Iterable[str], branch_id: str | None = None, learner_id: str | None = None) -> str:
    """Convenience helper used by offline tests and the local CLI only."""
    return issue_agent_token(signing_key, subject=subject, audience=audience, capabilities=capabilities, branch_id=branch_id, learner_id=learner_id)
