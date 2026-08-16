# Zodiac Bank Phases 2–4 Security Controls

These phases extend the Phase 1 identity and capability boundary while keeping
all training state synthetic, local, bounded, and side-effect-free.

## Phase 2 — fraud intelligence and telemetry

### Synthetic fraud engine

` scripts/zodiac_fraud_engine.py ` provides deterministic metadata-only risk
scoring for virtual operations. Signals include:

- high-value and very-high-value amount bands;
- high-risk customers and incomplete synthetic KYC;
- monitored beneficiaries;
- untrusted device and stale-session signals;
- new beneficiaries;
- transaction velocity and source fan-out;
- cash-intensity signals.

The result is `allow`, `review`, or `deny` with explainable weighted signals.
A review assessment adds evidence and approval pressure; a deny assessment cannot
reach settlement.

The mule graph contains account IDs, aggregate edges, and bounded hub metadata;
it never stores raw transactions or customer content.

### Structured telemetry

` scripts/zodiac_telemetry.py ` provides:

- versioned event envelopes;
- trace and operation correlation;
- bounded payloads;
- secret/token/prompt/raw-content redaction;
- event hashes;
- thread-safe bounded storage;
- aggregate metrics;
- deterministic alert correlation.

The virtual bank now exposes security-event and fraud-risk metadata in its local
snapshot and memory context. Detection rule `ZB-FRAUD-001` covers high-risk
synthetic transaction review and mule-network signals.

## Phase 3 — sandbox and privacy

### No-egress handler sandbox

` scripts/zodiac_sandbox.py ` intentionally executes only registered pure Python
handlers. It does not interpret shell strings, launch subprocesses, open sockets,
or read the host filesystem.

Controls include:

- tool allowlists;
- typed JSON arguments;
- call, input, output, and time budgets;
- fixture-only filesystem access;
- path normalization and traversal rejection;
- explicit `network_allowed: false` metadata;
- empty side-effect lists.

Secure MCP calls use this handler sandbox by default. Secure stdio remains
explicitly disabled until a future process-isolation phase is designed and
verified.

### Privacy and data governance

` scripts/zodiac_privacy.py ` provides:

- public/internal/sensitive/restricted classifications;
- purpose-bound access decisions;
- branch and role scope checks;
- restricted-role enforcement;
- field projection and redaction hashes;
- bounded retention purge;
- privacy-safe access audit events;
- aggregate privacy metrics without raw content.

Bank memory retrieval applies this policy before returning records. Existing
branch scoping remains enforced independently, so privacy authorization cannot
expand graph or RAG scope.

## Phase 4 — resilience and evaluation

### Recovery controls

` scripts/zodiac_resilience.py ` and the orchestrator provide:

- tamper-evident bounded checkpoints;
- monotonic recovery sequence checks;
- circuit breakers;
- a human/instructor kill switch;
- virtual double-entry ledger reconciliation;
- recovery responses that do not mutate the ledger.

New challenge endpoints are:

```text
POST /api/bank/operations/{run_id}/checkpoint
POST /api/bank/checkpoints/{checkpoint_id}/recover
```

### Held-out evaluation

` scripts/zodiac_evaluation.py ` evaluates the range without model calls or
network access. It checks:

- 100 scenarios and 50 gates;
- complete detection/control metadata;
- harmless mutation variants;
- canonicalization transfer;
- precision/recall on held-out fixtures;
- deterministic fixture hashes.

The evaluator explicitly reports `model_calls: 0` and `external_egress: false`.

## Validation

```bash
python3 scripts/zodiac_bank_eval.py
python3 scripts/zodiac_bank_progression_test.py
python3 scripts/validate_zodiac_bank.py
```

The existing 100-scenario/50-gate curriculum remains the authority for learner
progression. These phases add executable control surfaces and regression checks;
they do not expose real payment, identity, customer, or external-system data.
