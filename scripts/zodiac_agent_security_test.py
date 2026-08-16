#!/usr/bin/env python3
"""Offline regression tests for Zodiac Bank Phase 1 agent security."""

from __future__ import annotations

import secrets

from zodiac_agent_security import (
    AgentSecurityError,
    ReplayGuard,
    delegate_token,
    issue_agent_token,
    manifest_digest,
    validate_tool_call,
    verify_agent_token,
    verify_request,
)


KEY = secrets.token_bytes(32)
TOOLS = [
    {
        "name": "memory",
        "description": "Read or write synthetic memory",
        "inputSchema": {
            "type": "object",
            "properties": {"operation": {"type": "string"}, "key": {"type": "string"}},
            "required": ["operation"],
        },
    }
]


def expect_failure(fn: object, label: str) -> None:
    try:
        fn()  # type: ignore[operator]
    except AgentSecurityError:
        return
    raise AssertionError(f"{label}: expected AgentSecurityError")


def main() -> int:
    digest = manifest_digest(TOOLS)
    token = issue_agent_token(
        KEY,
        subject="teller-north",
        audience="mcp-wrapper",
        branch_id="ZB-BR-001",
        learner_id="phase1",
        manifest=digest,
        capabilities=["mcp.tools.list", "mcp.tool.call", "mcp.tool.memory", "delegation.*"],
        now=1_000,
        ttl_seconds=300,
    )
    claims = verify_agent_token(token, KEY, audience="mcp-wrapper", required_capability="mcp.tools.list", manifest=digest, now=1_001)
    assert claims["sub"] == "teller-north"
    expect_failure(lambda: verify_agent_token(token, KEY, audience="a2a-router", now=1_001), "audience binding")
    expect_failure(lambda: verify_agent_token(token, KEY, audience="mcp-wrapper", required_capability="mcp.tool.filesystem", manifest=digest, now=1_001), "capability binding")
    changed_digest = manifest_digest([{**TOOLS[0], "description": "changed"}])
    expect_failure(lambda: verify_agent_token(token, KEY, audience="mcp-wrapper", manifest=changed_digest, now=1_001), "manifest binding")

    guard = ReplayGuard()
    verified = verify_request(token, KEY, guard, request_nonce="req-1", audience="mcp-wrapper", required_capability="mcp.tools.list", manifest=digest, now=1_001)
    assert verified["jti"] == claims["jti"]
    expect_failure(lambda: verify_request(token, KEY, guard, request_nonce="req-1", audience="mcp-wrapper", required_capability="mcp.tools.list", manifest=digest, now=1_001), "request replay")

    # The public helper obtains the current clock; verify the
    # narrower child with a real current-time token to avoid a fake clock mismatch.
    current_token = issue_agent_token(KEY, subject="support-router", audience="a2a-router", capabilities=["delegation.*", "a2a.delegate"], manifest=digest)
    current_claims = verify_agent_token(current_token, KEY, audience="a2a-router", required_capability="a2a.delegate", manifest=digest)
    child = delegate_token(KEY, current_claims, subject="support-router", audience="a2a-knowledge", capabilities=["knowledge.query"])
    child_claims = verify_agent_token(child, KEY, audience="a2a-knowledge", required_capability="knowledge.query")
    assert child_claims["parent"] == current_claims["jti"] and child_claims["depth"] == 1
    expect_failure(lambda: verify_agent_token(child, KEY, audience="a2a-knowledge", required_capability="a2a.delegate"), "delegation narrowing")

    call = validate_tool_call(TOOLS, "memory", {"operation": "read", "key": "synthetic"}, allowed_tools={"memory"})
    assert call["manifest"] == digest and call["side_effects"] == []
    expect_failure(lambda: validate_tool_call(TOOLS, "memory", {"operation": "read", "unknown": "x"}, allowed_tools={"memory"}), "undeclared argument")
    expect_failure(lambda: validate_tool_call(TOOLS, "memory", {"operation": 7}, allowed_tools={"memory"}), "typed argument")
    expect_failure(lambda: validate_tool_call(TOOLS, "filesystem", {}, allowed_tools={"memory"}), "tool allowlist")

    print("Phase 1 agent security regression: PASS")
    print("- signed identity, audience, branch, learner, and manifest binding")
    print("- capability narrowing and delegated child token")
    print("- request nonce replay rejection")
    print("- typed MCP arguments and tool allowlist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
