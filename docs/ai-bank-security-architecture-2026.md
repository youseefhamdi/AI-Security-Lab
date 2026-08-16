# Zodiac Bank 2026 AI-Security Architecture and Research Plan

Updated: 2026-08-16

Zodiac Bank is a **synthetic security-training bank**, not a real financial institution. It has virtual balances and local workflow state so learners can investigate realistic authorization and AI-agent failures without touching real money, accounts, payment rails, or external systems.

## Research synthesis

### 1. Agent-mediated payments need deterministic boundaries

The IMF's 2026 note on agentic payments describes the shift from human-initiated instructions to agent-mediated decisions and separates **intent, authorization, and settlement**. It highlights traceability, opacity, cybersecurity, legal uncertainty, and resilience risks. The lab implements this separation directly:

- the employee loop creates an intent;
- the orchestrator and explicit employee approvals authorize it;
- only the in-memory virtual ledger settles it;
- the receipt and append-only event chain provide traceability.

Source: [IMF, How Agentic AI Will Reshape Payments](https://www.elibrary.imf.org/view/journals/068/2026/004/article-A001-en.xml), 2026-04-24.

### 2. Financial-services AI governance must be operational

The U.S. Treasury released the Financial Services AI Risk Management Framework and AI Lexicon in February 2026. Treasury describes lifecycle risk evaluation, accountability, transparency, resilience, identity, fraud, explainability, and data practices as implementation concerns rather than aspirational principles. The lab maps those concerns to machine-checkable controls and evidence requirements.

Source: [U.S. Treasury, Financial Services AI Risk Management Framework](https://home.treasury.gov/news/press-releases/sb0401), 2026-02-19.

### 3. Agent systems add novel security failure modes

NIST's January 2026 AI-agent RFI identifies autonomous actions, multi-agent orchestration, indirect prompt injection, data poisoning, backdoors, specification gaming, and confidentiality/integrity/availability risks. It also calls for security measurement, secure development, deployment controls, and monitoring. The range therefore tests the full chain: model input, RAG, memory, tools, employee identity, branch scope, approvals, loops, and settlement.

Source: [NIST CAISI, Request for Information on Security Considerations for AI Agents](https://www.federalregister.gov/documents/2026/01/08/2026-00206/request-for-information-regarding-security-considerations-for-artificial-intelligence-agents), 2026-01-08.

### 4. Agentic risk is a system property

OWASP's 2026 Top 10 for Agentic Applications is peer-reviewed by more than 100 practitioners and is explicitly aimed at systems that plan, act, and make decisions across workflows. The lab treats prompt injection, tool misuse, excessive agency, memory/context poisoning, identity abuse, insecure inter-agent communication, and supply-chain drift as composable risks rather than isolated chatbot bugs.

Source: [OWASP Top 10 for Agentic Applications 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/), 2025-12-09, resource page updated 2026-08-03.

### 5. Faster payments amplify fraud and mule-network risk

The 2026 BIS/CPMI report on fraud in fast payments and current 2026 financial-sector reporting emphasize faster settlement, cross-border fraud, mule accounts, and the need for bank-verified customer attributes and coordinated controls. The lab represents these as synthetic high-risk customers, branches, account graphs, AML loops, risk-based approvals, and transaction-behavior scenarios. It never models or enables a real payment rail.

Source: [BIS/CPMI, Enhancing cross-border payments – addressing fraud](https://www.bis.org/cpmi/pietf/fraud_report_2026.pdf), 2026.

## Implemented reference architecture

```text
Synthetic canonical bank data
  branches · staff/employees · customers · accounts · cases
                         │
                         ▼
              Zodiac Bank Memory
  virtual balances · operation intents · approvals · ledger · receipts
                         │
             ┌───────────┴───────────┐
             ▼                       ▼
       Graph + RAG context     Employee loop orchestrator
       provenance/evidence     bounded route/retries/state
             │                       │
             └───────────┬───────────┘
                         ▼
              Human-style approval gates
        maker/checker · role · branch · risk · amount
                         │
                         ▼
              In-memory virtual settlement only
       no external egress · no real money · no side effects
```

The implementation is split into:

- `bank-data/zodiac-bank.json`: canonical branches, staff, customers, products, accounts, and cases;
- `bank-data/financial-operations.json`: employees, virtual account seed balances, operation types, limits, approval policies, and employee-loop routes;
- `scripts/zodiac_bank_simulator.py`: in-memory bank memory, authorization, virtual ledger, receipt, and provenance rules;
- `scripts/zodiac_bank_orchestrator.py`: bounded employee-loop planning, RAG/memory context, approvals, and local audit state;
- `bank-data/workflows.json` and `orchestrator-config/zodiac-bank.json`: symmetric worker/workflow registries;
- `rag-docs/Zodiac_Bank_Operations.md`: local policy evidence for RAG and context exercises;
- `training-config/bank-profiles.json`: learner-specific dynamic security posture promoted by accepted stage flags.

## Security invariants

The evaluator enforces these invariants:

1. **Maker/checker:** the initiating employee cannot approve their own operation.
2. **Role binding:** only declared employee roles can approve a given operation.
3. **Branch isolation:** teller and branch-manager workers cannot operate on another branch's account.
4. **Risk escalation:** high-value and high-risk customer operations require additional distinct roles.
5. **Idempotency:** reusing an operation ID with changed parameters is rejected; settling a committed operation twice is rejected.
6. **No premature mutation:** pending operations cannot change virtual balances.
7. **Balanced settlement:** transfer, receive, and withdrawal create paired virtual ledger entries.
8. **Receipt integrity:** every committed operation creates one immutable synthetic receipt with a hash.
9. **Bounded loops:** every route has a maximum step count and retry budget.
10. **Provenance:** graph, RAG, and memory evidence is data only; it cannot authorize a financial action.
11. **Privacy:** staff/customer records are synthetic and branch-scoped in memory retrieval.
12. **No external side effects:** every profile, workflow, and operation denies external egress.

## Security audit hardening completed

The post-implementation audit found and corrected these concrete weaknesses:

- **Cross-learner loop authorization:** employee-loop IDs are bound to their learner owner, and operation IDs cannot be claimed by another learner.
- **Branch approval confusion:** teller and branch-manager approvals must match the operation branch; cross-branch account actions are rejected.
- **RAG tenant leakage:** branch-scoped workers receive branch-filtered graph and memory evidence, while the bank-wide Markdown corpus is redacted for those workers.
- **Shared learner state:** each learner receives a bounded, isolated virtual bank memory; the canonical bank fixture remains read-only shared configuration.
- **Concurrent settlement:** orchestrator mutations are serialized, so concurrent approvals cannot double-settle a ledger operation.
- **Account-state bypass:** restricted and monitored account policies are enforced before planning and add compliance/fraud escalation where applicable.
- **Flag promotion race:** the Training Gate re-checks stage completion after acquiring its SQLite write lock, preventing duplicate concurrent flag submissions from incrementing promotion state twice.
- **Orchestrator initialization race:** first-use creation of a learner's in-memory orchestrator is lock-protected so concurrent requests cannot replace state and lose pending operations.
- **Import portability:** bank simulator/orchestrator/context modules support both CLI execution and package-style imports for regression and service reuse.

These controls are covered by the offline evaluator's financial-bank, flag-progression, and runtime-security checks. No n8n integration is required at this stage; adding it later must remain an event-routing/approval UI layer and never become the ledger or authorization authority.

## Phase 1 implementation — agent identity and capability security

Phase 1 is now implemented in `scripts/zodiac_agent_security.py` and is shared by
secure MCP and A2A routes. It adds short-lived HMAC-signed `zbt1` tokens bound to
subject, audience, capabilities, expiry, branch/learner scope, delegation parent,
and (for MCP) the pinned tool-manifest digest. A bounded request-nonce replay
cache rejects duplicate requests.

- `mcp-wrapper` keeps the vulnerable `/tools/*` lesson surfaces and adds
  authenticated `/secure/tools/list` and `/secure/tools/call` routes.
- Secure MCP calls enforce manifest pinning, tool allowlists, typed arguments,
  required fields, capability checks, and default-deny execution through the
  no-egress handler sandbox. Stdio execution remains disabled.
- `a2a-router` adds `/secure/a2a`; it verifies the caller and delegates only the
  narrower `knowledge.query` capability to `a2a-knowledge`.
- `a2a-knowledge` verifies the child token at its own `/secure/a2a` boundary.
- `BankOrchestrator.plan()` and `.approve()` can bind calls to signed worker,
  branch, learner, audience, and operation capabilities; strict mode rejects
  missing agent tokens.
- `scripts/zodiac_agent_security_test.py` covers signature, audience, manifest,
  capability, delegation, replay, allowlist, and typed-argument failures.

Phase 1 deliberately does not provide a token-issuing HTTP endpoint, execute
untrusted commands, or make the model an authorization authority. The local
control plane or instructor tooling must issue tokens out of band.

## Phases 2–4 implementation — fraud, sandbox, privacy, resilience, and evaluation

The remaining roadmap phases are implemented in local modules and integrated with
virtual bank workflows:

- `scripts/zodiac_fraud_engine.py` provides explainable synthetic risk scoring,
  velocity/fan-out signals, and aggregate mule-network graphs.
- `scripts/zodiac_telemetry.py` provides bounded structured events, redaction,
  trace correlation, metrics, and deterministic alert correlation.
- `scripts/zodiac_sandbox.py` provides allowlisted handler execution with no
  shell, socket, host-filesystem, or unregistered-tool access.
- `scripts/zodiac_privacy.py` applies purpose, role, branch, classification,
  field-redaction, retention, and privacy-audit controls to bank memory.
- `scripts/zodiac_resilience.py` provides tamper-evident checkpoints, replay-safe
  recovery, circuit breakers, kill switches, and virtual-ledger reconciliation.
- `scripts/zodiac_evaluation.py` runs held-out mutation and transfer checks with
  zero model calls and zero external egress.

The challenge service exposes checkpoint/recovery routes, while secure MCP uses
the no-egress handler sandbox. Existing vulnerable lesson routes remain separate
from the hardened control paths.

## Top-tier roadmap

### Phase A — domain realism

- Add account lifecycle events, KYC/AML cases, branch cash-vault fixtures, card/token fixtures, and synthetic payment messages.
- Add operation state transitions: `intent → risk-review → approval → settlement → receipt → reconciliation`.
- Add reconciliation and rollback exercises with immutable event chains.

### Phase B — agent security

- Add agent identity and delegation tokens bound to worker, branch, operation, audience, nonce, and expiry.
- Add MCP/A2A payment tools with typed schemas, manifest digests, argument validation, and re-approval on drift.
- Add memory write review, summary lineage, retrieval conflict quarantine, and tenant-aware RAG filters.

### Phase C — fraud and resilience

- Add synthetic mule-network graphs, APP/social-engineering cases, velocity anomalies, device/session signals, and cross-branch correlation.
- Add deterministic circuit breakers, replay protection, duplicate detection, and bounded recovery workflows.
- Add model-risk evaluation: calibration, abstention, explanation evidence, fairness slices, and human override telemetry.

### Phase D — APT capstone

- Chain identity compromise, RAG poisoning, tool manipulation, memory persistence, transaction staging, detector evasion, and containment.
- Require independent evidence from graph, RAG, memory, ledger, employee approvals, and SIEM events.
- Grade both attacker discovery and defender response: detection latency, containment scope, recovery verification, residual risk, and lessons learned.

## How to run the current synthetic operations

Run a complete isolated receive and high-value transfer demonstration:

```bash
python3 scripts/zodiac_bank_orchestrator.py --demo
```

Create a pending receive operation:

```bash
python3 scripts/zodiac_bank_orchestrator.py \
  --operation receive \
  --actor teller-north \
  --amount-cents 25000 \
  --destination-account ZB-ACCT-1001 \
  --operation-id OP-TRAINING-001 \
  --audit-state logs/operations.sqlite3
```

The output gives a loop ID and route. Approvals must be explicit in the same process for the current CLI demonstration; the Python API is the reusable foundation for a service endpoint and browser workflow UI. All amounts are virtual integer cents and all data disappears with the process unless the optional local audit database is used.

Run the complete offline security evaluator:

```bash
python3 scripts/zodiac_bank_eval.py
python3 scripts/validate_zodiac_bank.py
```

The current implementation is intentionally conservative: a model may recommend, retrieve, summarize, or route, but only deterministic policy code and explicitly authorized synthetic employees can advance a virtual ledger.
