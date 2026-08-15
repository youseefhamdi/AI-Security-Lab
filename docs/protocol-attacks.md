# Real-World Protocol Attack Surfaces

These exercises are designed for the local, intentionally vulnerable AI Red Team Lab. All endpoints are expected to be localhost-bound and unauthenticated only inside the lab.

## A2A attack surfaces

### Agent Card enumeration

A2A agents commonly publish `/.well-known/agent.json`. An unauthenticated requester can enumerate names, versions, public URLs, capabilities, skills, input/output modes, and security hints. Cards may disclose internal hostnames, delegated agents, and trust assumptions before authentication occurs.

**Recon:** fetch the Router and Knowledge Agent Cards with `exercises/protocol_recon.sh`.

**Defenses:** serve cards through an intentional discovery policy, avoid secrets and internal topology in public fields, authenticate sensitive skill use, validate card versions, and monitor unusual card enumeration.

### Trust relationship mapping

`delegatesTo`, `trustedBy`, skill examples, and endpoint URLs can reveal an agent graph. An attacker can use that graph to target the least-protected downstream agent or to create a confused-deputy chain.

**Test:** compare the Router card with the Knowledge card, then verify that Router delegation validates the destination, request identity, skill, and response provenance.

**Defenses:** use explicit allowlists, signed or pinned cards, mutual authentication, audience-bound tokens, capability checks, and response provenance.

## MCP attack surfaces

### Tool schema enumeration

The wrapper intentionally exposes `/tools/list` without authentication. Tool names and JSON schemas reveal whether a server can access memory, filesystems, fetch URLs, databases, or other privileged resources.

**Recon:** enumerate `/tools/list` and record argument names, formats, and descriptions.

**Defenses:** authenticate schema access where appropriate, minimize descriptions, separate public and privileged tools, and never treat a schema as an authorization decision.

### Unauthenticated tool invocation

The wrapper intentionally permits `/tools/call` without authentication. An attacker can invoke a tool directly, supply unexpected arguments, or use a low-risk tool as a bridge to a higher-trust stdio process.

**Recon:** call the simulated memory tool, then repeat against configured tools in an authorized environment.

**Defenses:** enforce identity and per-tool authorization at the wrapper, validate arguments server-side, sandbox stdio processes, apply timeouts and output limits, and log every invocation.

## Cross-protocol attacks

### A2A delegation to MCP without validation

A Router may accept an A2A request, classify it, and delegate to a Knowledge Agent or MCP tool. If the Router trusts model output or downstream tool names without validation, an attacker can cause:

1. A2A Agent Card discovery to reveal the delegation graph.
2. Prompt injection to change the intended skill or tool.
3. The Router to invoke an MCP filesystem, fetch, or memory tool.
4. The tool response to be returned as trusted agent content.

This is a confused-deputy path: the caller lacks direct privilege but obtains it through an agent's delegation.

**Defenses:** bind each skill to an explicit tool allowlist, validate structured intents before delegation, require user authorization for side effects, pass identity and audience claims across protocols, treat tool output as untrusted data, and maintain an end-to-end audit trail.

## Static and runtime separation

On the build-only VPS, use only syntax and file checks. Run `RUNTIME=1 ./exercises/protocol_recon.sh` only on the isolated local lab after the services are intentionally started.
