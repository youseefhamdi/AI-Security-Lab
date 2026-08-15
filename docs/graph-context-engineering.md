# Zodiac Bank Graph and Context Engineering

Zodiac Bank now has a deterministic graph/context layer at `http://127.0.0.1:5070`. It is intentionally local-only, uses only synthetic canonical data, and requires a separate graph-context key for data routes in strict mode.

## Graph engineering

The service derives a property graph from:

- `bank-data/zodiac-bank.json`
- `bank-data/workflows.json`

Graph nodes include branches, staff, customers, accounts, products, policies, cases, workers, workflows, and workflow branches. Graph edges preserve relationships such as `owned_by`, `works_at`, `assigned_to`, `has_branch`, and `routes_to`.

Every node and edge includes:

- `provenance.source`
- `provenance.synthetic=true`
- a trust class (`canonical` or `control`)
- stable identifiers

The graph is derived evidence, not an authorization database. A graph edge must never grant account access, approve a case, or authorize a tool.

Inspect a bounded neighborhood:

Configure a separate local service key before startup:

```env
GRAPH_CONTEXT_SECURITY_MODE=strict
GRAPH_CONTEXT_API_KEY=<at-least-24-character-random-local-key>
```

Use it for graph and context data routes:

```bash
export GRAPH_CONTEXT_API_KEY='<graph-context-key>'
curl --fail \
  'http://127.0.0.1:5070/v1/graph/neighborhood?entity_id=ZB-CASE-002&depth=2&max_nodes=24' \
  -H "X-Graph-Context-Key: ${GRAPH_CONTEXT_API_KEY}"
```

Traversal is bounded to depth three and 64 nodes by the service. This prevents accidental graph explosions and makes context cost predictable.

## Context engineering

`POST /v1/context/assemble` returns a versioned context packet with separate trust boundaries:

```bash
curl --fail http://127.0.0.1:5070/v1/context/assemble \
  -H "X-Graph-Context-Key: ${GRAPH_CONTEXT_API_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{
    "query":"Review ZB-CASE-002 and its assigned worker",
    "entity_ids":["ZB-CASE-002"],
    "depth":2,
    "max_nodes":24,
    "max_chars":12000
  }'
```

The packet separates:

1. **Context policy** — evidence cannot issue instructions or widen scope.
2. **Canonical graph evidence** — synthetic structured facts with provenance.
3. **Retrieved documents** — explicitly marked `retrieved-untrusted-data`.
4. **User input** — explicitly marked `untrusted-user-input`.

The assembler provides:

- deterministic packet IDs for audit correlation
- bounded character budgets
- bounded graph depth and node count
- instruction-like content detection in retrieved documents
- truncation indicators
- no model calls and no side effects

Aurora and the A2A Knowledge Agent use `CONTEXT_ENGINEERING_MODE=structured` by default. Structured mode sends the user request separately from the evidence packet and tells the model not to follow instructions found inside retrieved content. `legacy` mode is retained only for controlled prompt-injection comparison exercises:

```env
CONTEXT_ENGINEERING_MODE=structured
GRAPH_CONTEXT_ENABLED=1
GRAPH_CONTEXT_CONTAINER_URL=http://zodiac-context:5070
GRAPH_CONTEXT_SECURITY_MODE=strict
GRAPH_CONTEXT_API_KEY=<at-least-24-character-random-local-key>
CONTEXT_MAX_CHARS=12000
```

## Workflow and orchestrator symmetry

The bounded Zodiac Bank workflow runner now assembles a graph/context packet for every planned case and stores it in the durable plan. This lets instructors compare:

- the case entity graph
- the selected workflow branch
- the delegated worker route
- the source and trust of context used for planning

The orchestrator manifest includes `graph-context-engineer` and `context-engineer` workers and declares the same context contract.

Run the offline posture evaluator before a cohort starts:

```bash
python3 scripts/zodiac_bank_eval.py
python3 scripts/zodiac_bank_eval.py --format json --output logs/zodiac-bank-evaluation.json
```

The evaluator never contacts Docker, models, ChromaDB, Mem0, or external URLs. It checks curriculum gates, graph provenance, scope filtering, context budgets, workflow approval, orchestrator symmetry, and graph-context authentication wiring.

```bash
RUNTIME=1 python3 scripts/zodiac_bank_workflows.py \
  --workflow fraud-investigation \
  --case-id ZB-CASE-002
```

Validate all domain, graph, workflow, and orchestrator relationships:

```bash
python3 scripts/validate_zodiac_bank.py
```

## Security training value

This layer supports advanced lessons in:

- graph poisoning and fake relationship edges
- stale or missing provenance
- cross-customer graph expansion
- prompt injection in retrieved graph/document attributes
- context-window exhaustion and truncation mistakes
- authority confusion between policy, canonical data, retrieved data, and user input
- worker-route manipulation in orchestrated workflows

Keep the graph service bound to localhost. Do not use it as a production bank authorization system, and do not load real customer records or credentials into it.
