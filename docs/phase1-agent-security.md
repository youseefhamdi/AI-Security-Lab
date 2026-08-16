# Zodiac Bank Phase 1 — Agent Identity and Capability Security

Phase 1 hardens the agent-to-agent and MCP boundaries while preserving the
legacy vulnerable routes used by the training curriculum.

## Security model

The local control plane issues short-lived HMAC-signed `zbt1` tokens. A token is
bound to:

- subject (`sub`);
- audience (`aud`);
- capabilities (`cap`);
- issue and expiry time;
- unique token ID (`jti`);
- optional branch and learner scope;
- optional parent delegation ID;
- optional MCP manifest digest.

Every protected request also requires a fresh request nonce. A bounded in-memory
replay guard rejects reuse within the token lifetime.

This is a deterministic training mechanism, not a production identity provider.
The signing key must be supplied through `ZODIAC_AGENT_SIGNING_KEY`; it is never
committed to the repository.

## Hardened surfaces

### MCP wrapper

Legacy lesson routes remain intentionally vulnerable:

```text
GET/POST /tools/list
POST     /tools/call
```

The Phase 1 routes are:

```text
GET  /secure/tools/list
POST /secure/tools/call
```

Required headers:

```text
X-Zodiac-Agent-Token: <short-lived token>
X-Zodiac-Request-Nonce: <fresh request nonce>
```

The secure path verifies the pinned manifest digest, tool allowlist, capability
for the specific tool, required fields, declared argument types, and bounded
argument sizes. Only `memory` is allowlisted by default. Secure calls use the
Phase 3 no-egress handler sandbox; secure stdio execution remains disabled.

### A2A delegation

The router exposes `/secure/a2a`. It requires an `a2a-router` audience and the
`a2a.delegate` capability. When it delegates a knowledge request, it creates a
narrower child token for the `a2a-knowledge` audience with only
`knowledge.query`. The Knowledge Agent verifies that child token on its own
`/secure/a2a` route.

The original `/` and `/a2a` routes remain available for the intentionally
vulnerable protocol lessons.

### Bank orchestrator

`BankOrchestrator.plan()` and `.approve()` accept optional:

```python
agent_token="..."
agent_request_nonce="..."
```

When supplied, the token is bound to the exact worker, branch, learner, audience,
and operation capability. In `ZODIAC_AGENT_SECURITY_MODE=strict`, missing tokens
are rejected. Existing offline curriculum calls remain compatible in development
mode.

### Secure bank operations

The challenge service also exposes signed-token variants for the synthetic
orchestrator:

```text
POST /api/secure/bank/operations/plan
POST /api/secure/bank/operations/{run_id}/approve
```

They require the learner token plus the two agent headers. The planning token is
bound to `bank.operation.plan` and the initiating worker; the approval token is
bound to `bank.operation.approve` and the approving worker. The existing
`/api/bank/operations/*` routes remain the compatibility surface for the
curriculum harness.

## Environment

For protocol services, configure a random local key of at least 32 bytes:

```bash
export ZODIAC_AGENT_SIGNING_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
export AGENT_SECURITY_MODE=strict
export MCP_SECURE_ALLOWED_TOOLS=memory
export MCP_SECURE_STDIO_ENABLED=0
```

The Compose services receive the key through environment interpolation. Do not
place real credentials in `.env`, scenario fixtures, logs, or learner evidence.

## Offline verification

```bash
PYTHONPATH=scripts python3 scripts/zodiac_agent_security_test.py
python3 -m py_compile scripts/zodiac_agent_security.py scripts/zodiac_agent_security_test.py
```

The legacy challenge routes are not converted automatically: they remain
explicitly labeled training surfaces. New secure scenarios should target the
`/secure/*` routes and assert both positive authorization and negative paths:
wrong audience, expired token, manifest drift, capability escalation, branch
confusion, learner confusion, duplicate nonce, and malformed tool arguments.
