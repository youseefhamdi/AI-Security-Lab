"""Deliberately unauthenticated HTTP wrapper for MCP stdio tools.

This wrapper is intentionally exposed for protocol reconnaissance exercises.
Configure stdio commands through environment variables only in the lab.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(title="Zodiac Bank MCP HTTP Wrapper", version="1.0")

TOOLS = [
    {"name": "memory", "description": "Read or write the configured memory MCP server", "inputSchema": {"type": "object", "properties": {"operation": {"type": "string"}, "key": {"type": "string"}, "value": {"type": "string"}}}},
    {"name": "filesystem", "description": "Read a file through the configured filesystem MCP server", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "fetch", "description": "Fetch a URL through the configured fetch MCP server", "inputSchema": {"type": "object", "properties": {"url": {"type": "string", "format": "uri"}}, "required": ["url"]}},
]

COMMANDS = {
    "memory": os.environ.get("MCP_MEMORY_COMMAND", ""),
    "filesystem": os.environ.get("MCP_FILESYSTEM_COMMAND", ""),
    "fetch": os.environ.get("MCP_FETCH_COMMAND", ""),
}


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


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "healthy", "tools_count": len(TOOLS), "auth": "none"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "3000")))
