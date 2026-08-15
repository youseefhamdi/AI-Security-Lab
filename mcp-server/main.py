"""Deliberately vulnerable MCP server for Unit 2.1 practice.
Exposes tool schemas without authentication (common misconfiguration).
"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import os

app = FastAPI(title="NovaTech MCP Server", version="1.0")

TOOLS_CATALOG = {
    "tools": [
        {
            "name": "search_documents",
            "description": "Search internal knowledge base",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "top_k": {"type": "integer", "default": 5}
                },
                "required": ["query"]
            }
        },
        {
            "name": "execute_sql",
            "description": "Execute SQL query against internal database",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "sql": {"type": "string"}
                },
                "required": ["sql"]
            }
        },
        {
            "name": "send_email",
            "description": "Send email via corporate SMTP",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "format": "email"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"}
                },
                "required": ["to", "subject", "body"]
            }
        },
        {
            "name": "read_file",
            "description": "Read file from internal filesystem (restricted)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"}
                },
                "required": ["path"]
            }
        }
    ]
}

@app.get("/")
@app.get("/mcp")
async def mcp_info():
    return {"protocol": "mcp", "version": "1.0", "server": "novatech-internal/1.0"}

@app.get("/mcp/tools/list")
@app.post("/mcp/tools/list")
async def list_tools():
    """VULNERABLE: no auth required"""
    return JSONResponse(
        content=TOOLS_CATALOG,
        headers={
            "X-MCP-Server": "novatech-internal/1.0",
            "X-Internal-Only": "true"
        }
    )

@app.post("/mcp/tools/call")
async def call_tool(request: Request):
    body = await request.json()
    tool = body.get("tool")
    params = body.get("params", {})
    
    if tool == "search_documents":
        return {"result": f"Found 3 documents matching '{params.get('query')}'", "documents": ["PTO_Policy", "Architecture_Overview", "Employee_Handbook"]}
    if tool == "execute_sql":
        return {"result": "Query executed", "rows": [{"id": 1, "name": "admin", "email": "admin@novatech-internal.com"}]}
    if tool == "send_email":
        return {"result": f"Email queued for delivery to {params.get('to')}"}
    if tool == "read_file":
        return {"error": "Permission denied", "path": params.get("path")}
    return {"error": f"Unknown tool: {tool}"}

@app.get("/health")
async def health():
    return {"status": "healthy", "tools_count": len(TOOLS_CATALOG["tools"])}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3000)
