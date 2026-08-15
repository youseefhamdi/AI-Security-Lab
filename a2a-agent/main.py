"""A2A-compliant agent with Agent Card — Unit 2.1 self-describing protocol."""
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(title="NovaTech A2A Agent")

AGENT_CARD = {
    "name": "NovaTech Support Orchestrator",
    "version": "2.1.0",
    "description": "Multi-agent orchestrator for customer support workflows",
    "endpoint": "http://aurora.novatech-internal.com:5000",
    "capabilities": {
        "streaming": True,
        "pushNotifications": False,
        "stateTransitionHistory": True
    },
    "skills": [
        {
            "id": "ticket_routing",
            "name": "Route Support Tickets",
            "description": "Classifies and routes support tickets to appropriate team",
            "tags": ["support", "classification"],
            "examples": ["Route ticket #1234 to engineering team"]
        },
        {
            "id": "knowledge_lookup",
            "name": "Knowledge Base Lookup",
            "description": "Retrieves relevant internal documentation",
            "tags": ["rag", "retrieval"],
            "examples": ["Find policy about vacation days"]
        },
        {
            "id": "escalation",
            "name": "Ticket Escalation",
            "description": "Escalates tickets to human agents or managers",
            "tags": ["workflow", "human-in-the-loop"],
            "examples": ["Escalate ticket #5678 to senior support"]
        }
    ],
    "trust": {
        "delegatesTo": [
            "aurora.novatech-internal.com",
            "phoenix.novatech-internal.com"
        ],
        "trustedBy": [
            "api-gateway.novatech-internal.com",
            "web-portal.novatech-internal.com"
        ],
        "authMethod": "bearer_token",
        "internalApiKeyHint": "ntk_prod_"
    },
    "defaultInputModes": ["text/plain", "application/json"],
    "defaultOutputModes": ["text/plain", "application/json"]
}

@app.get("/.well-known/agent.json")
async def agent_card():
    return JSONResponse(content=AGENT_CARD)

@app.get("/health")
async def health():
    return {"status": "healthy", "a2a_version": "1.0"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=4000)
