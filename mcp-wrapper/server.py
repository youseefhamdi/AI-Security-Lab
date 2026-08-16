"""Zodiac Bank MCP HTTP wrapper.

The legacy ``/tools/*`` routes remain intentionally unauthenticated for the
protocol-reconnaissance lesson. Phase 1 adds hardened ``/secure/*`` routes that
require a short-lived signed agent token, a request nonce, a pinned manifest,
and typed tool arguments. Secure routes never execute stdio commands unless the
explicit later-sandbox switch is enabled.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

SHARED_DIR = Path("/app/scripts")
if not SHARED_DIR.is_dir():
    SHARED_DIR = Path(__file__).resolve().parent.parent / "scripts"
if SHARED_DIR.is_dir() and str(SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_DIR))

from zodiac_agent_security import (  # noqa: E402
    AgentSecurityError,
    ReplayGuard,
    manifest_digest,
    validate_tool_call,
    verify_request,
)
from zodiac_sandbox import LocalToolSandbox, SandboxViolation, memory_handler  # noqa: E402

app = FastAPI(title="Zodiac Bank MCP HTTP Wrapper", version="1.1")

TOOLS = [
    {"name": "memory", "description": "Read or write the configured memory MCP server", "inputSchema": {"type": "object", "properties": {"operation": {"type": "string"}, "key": {"type": "string"}, "value": {"type": "string"}}, "required": ["operation"]}},
    {"name": "filesystem", "description": "Read a file through the configured filesystem MCP server", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "fetch", "description": "Fetch a URL through the configured fetch MCP server", "inputSchema": {"type": "object", "properties": {"url": {"type": "string", "format": "uri"}}, "required": ["url"]}},
]

COMMANDS = {
    "memory": os.environ.get("MCP_MEMORY_COMMAND", ""),
    "filesystem": os.environ.get("MCP_FILESYSTEM_COMMAND", ""),
    "fetch": os.environ.get("MCP_FETCH_COMMAND", ""),
}
DEFAULT_SIGNING_KEY = "zodiac-bank-agent-signing-key-change-me"
SIGNING_KEY_VALUE = os.environ.get("ZODIAC_AGENT_SIGNING_KEY", DEFAULT_SIGNING_KEY)
AGENT_SECURITY_MODE = os.environ.get("AGENT_SECURITY_MODE", "development").lower()
SECURE_STDIO_ENABLED = os.environ.get("MCP_SECURE_STDIO_ENABLED", "0") == "1"
SANDBOX_ENABLED = os.environ.get("MCP_SANDBOX_ENABLED", "1") == "1"
SECURE_SANDBOX = LocalToolSandbox()
if "memory" in SECURE_SANDBOX.policy.allowed_tools:
    SECURE_SANDBOX.register("memory", memory_handler)
SECURE_ALLOWED_TOOLS = {
    item.strip() for item in os.environ.get("MCP_SECURE_ALLOWED_TOOLS", "memory").split(",") if item.strip()
}
MANIFEST_DIGEST = manifest_digest(TOOLS)
REPLAY_GUARD = ReplayGuard()
if AGENT_SECURITY_MODE == "strict" and (SIGNING_KEY_VALUE == DEFAULT_SIGNING_KEY or len(SIGNING_KEY_VALUE.encode("utf-8")) < 32):
    raise RuntimeError("strict MCP security requires ZODIAC_AGENT_SIGNING_KEY with at least 32 bytes")


def run_stdio_tool(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    command = COMMANDS.get(tool, "")
    if not command:
        return {"tool": tool, "mode": "simulated", "arguments": arguments, "result": "stdio command not configured"}

    try:
        completed = subprocess.run(
            shlex.split(command),
            input=json.dumps({"method": "tools/call", "params": {"name": tool, "arguments": arguments}}),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"tool": tool, "error": str(exc)}
    return {"tool": tool, "returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}


def secure_claims(token: str, nonce: str, capability: str, *, subject: str | None = None) -> dict[str, Any]:
    if AGENT_SECURITY_MODE != "strict":
        # Development mode still verifies when a token is supplied, but keeps
        # local browser demos usable without provisioning a signing key.
        if not token:
            return {"sub": subject or "development-agent", "cap": [capability], "jti": "development", "exp": 2**31}
    try:
        return verify_request(
            token,
            SIGNING_KEY_VALUE,
            REPLAY_GUARD,
            request_nonce=nonce,
            audience="mcp-wrapper",
            required_capability=capability,
            subject=subject,
            manifest=MANIFEST_DIGEST,
        )
    except AgentSecurityError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@app.get("/tools/list")
@app.get("/mcp/tools/list")
async def list_tools() -> JSONResponse:
    """VULNERABLE: tool schemas are exposed without authentication."""
    return JSONResponse(content={"tools": TOOLS, "wrapper": "mcp-http-stdio/1.0"}, headers={"X-Internal-Only": "true"})


@app.post("/tools/call")
@app.post("/mcp/tools/call")
async def call_tool(body: dict[str, Any]) -> dict[str, Any]:
    """VULNERABLE: unauthenticated callers can invoke configured tools."""
    tool = body.get("name") or body.get("tool")
    arguments = body.get("arguments") or body.get("params") or {}
    if tool not in COMMANDS:
        return {"error": f"Unknown tool: {tool}", "available_tools": list(COMMANDS)}
    return run_stdio_tool(str(tool), arguments)


@app.get("/secure/tools/list")
async def secure_list_tools(
    x_zodiac_agent_token: str = Header(default="", alias="X-Zodiac-Agent-Token"),
    x_zodiac_request_nonce: str = Header(default="", alias="X-Zodiac-Request-Nonce"),
) -> JSONResponse:
    claims = secure_claims(x_zodiac_agent_token, x_zodiac_request_nonce, "mcp.tools.list")
    return JSONResponse(content={"tools": [tool for tool in TOOLS if tool["name"] in SECURE_ALLOWED_TOOLS], "manifest_digest": MANIFEST_DIGEST, "identity": {"subject": claims.get("sub"), "audience": claims.get("aud")}, "security": {"authenticated": True, "pinned_manifest": True, "side_effects": []}})


@app.post("/secure/tools/call")
async def secure_call_tool(
    body: dict[str, Any],
    x_zodiac_agent_token: str = Header(default="", alias="X-Zodiac-Agent-Token"),
    x_zodiac_request_nonce: str = Header(default="", alias="X-Zodiac-Request-Nonce"),
) -> dict[str, Any]:
    tool = str(body.get("name") or body.get("tool") or "")
    arguments = body.get("arguments") if "arguments" in body else body.get("params", {})
    claims = secure_claims(x_zodiac_agent_token, x_zodiac_request_nonce, "mcp.tool.call")
    try:
        call = validate_tool_call(TOOLS, tool, arguments, allowed_tools=SECURE_ALLOWED_TOOLS)
        capabilities = set(str(item) for item in claims.get("cap", []))
        if f"mcp.tool.{tool}" not in capabilities and "mcp.tool.*" not in capabilities:
            raise AgentSecurityError("agent lacks capability for this specific tool")
    except AgentSecurityError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    # Phase 1 proves authorization and argument boundaries. Actual stdio
    # execution remains disabled by default until the Phase 3 sandbox exists.
    result: dict[str, Any]
    if SANDBOX_ENABLED:
        try:
            sandbox_result = SECURE_SANDBOX.execute(tool, arguments)
            result = {"tool": sandbox_result.tool, "mode": "no-egress-handler-sandbox", "status": sandbox_result.status, "output": sandbox_result.output, "elapsed_ms": sandbox_result.elapsed_ms, "network_allowed": sandbox_result.network_allowed, "filesystem_mode": sandbox_result.filesystem_mode}
        except SandboxViolation as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    elif SECURE_STDIO_ENABLED:
        result = run_stdio_tool(tool, arguments)
    else:
        result = {"tool": tool, "mode": "secure-simulated", "result": "execution withheld pending sandbox policy"}
    return {"accepted": True, "identity": {"subject": claims.get("sub"), "jti": claims.get("jti")}, "call": call, "result": result, "side_effects": []}


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "healthy", "tools_count": len(TOOLS), "auth": "signed-agent-token" if AGENT_SECURITY_MODE == "strict" else "development-or-signed-agent-token", "manifest_digest": MANIFEST_DIGEST, "secure_allowed_tools": sorted(SECURE_ALLOWED_TOOLS), "sandbox_enabled": SANDBOX_ENABLED, "secure_stdio_enabled": SECURE_STDIO_ENABLED}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "3000")))
