# Zodiac Bank AI Security Curriculum

Zodiac Bank is a synthetic, localhost-only banking AI security lab. It is designed for authorized training, not testing real banks, accounts, models, credentials, or external infrastructure.

## Progression model

The curriculum is defined in `training-config/curriculum.json` and enforced by the Training Gate at `http://127.0.0.1:5050`.

A learner must:

1. Read the current lesson and confirm the authorized scope.
2. Exercise only the listed local target services.
3. Preserve request/response evidence and explain the impact.
4. Find the current stage's hard flag through the intended vulnerable lab surface.
5. Submit the flag to `/api/flags/submit`.
6. Receive the next unlocked stage.

The curriculum endpoint intentionally returns stage metadata and flag format, but never the plaintext hard flag. Only the first incomplete stage is unlocked; later stages remain locked even if a learner knows their flag. Each unlocked lesson exposes three safe hints in escalating order. Invalid attempts are hashed for audit and bounded per stage. Progress is stored in a local SQLite database inside the `training_data` volume. The learner artifact is only an opaque current-stage pointer and never contains a flag.

## Levels

| Stage | Level | Focus | Main targets |
| --- | --- | --- | --- |
| L00 | Foundation | Scope, assets, trust boundaries | Training Gate, Aurora |
| L01 | Beginner | Endpoint and model reconnaissance | Aurora, Phoenix, Assistant |
| L02 | Intermediate | Direct and indirect prompt injection | Aurora |
| L03 | Intermediate | RAG provenance and poisoning | Aurora, Knowledge Agent, ChromaDB, LightRAG |
| L04 | Advanced | A2A/MCP confused deputy, tool poisoning, rug pulls | A2A Router, Knowledge Agent, MCP |
| L05 | Advanced | Memory poisoning and tenant isolation | Mem0, Aurora, MCP memory |
| L06 | Expert | Identity, post-compromise discovery, and AI control plane | Kong, Aurora, A2A, Mem0 |
| L07 | Expert | Model, dependency, artifact, and agentic supply chain | Dependency Sweeper, Orchestrator, Filebeat |
| L08 | Red Team | Adaptive evasion, volume anomalies, and SIEM detection | Aurora, Elasticsearch, Kibana, Filebeat |
| L09 | APT Simulation | AI-orchestrated campaign, approvals, and containment | All local services |

## Unique challenge surfaces

The challenge surface is deliberately separate from the progression API. It is available only on the local lab network and has one discovery mechanic per stage:

| Stage | Discovery mechanic |
| --- | --- |
| L00 | Inspect response headers with `HEAD` scope reconnaissance. |
| L01 | Request verbose model inventory metadata. |
| L02 | Override the synthetic support instruction hierarchy. |
| L03 | Cross the published/draft retrieval boundary. |
| L04 | Abuse caller-controlled agent/tool delegation. |
| L05 | Query a different synthetic user's memory run. |
| L06 | Test proxy-supplied identity headers. |
| L07 | Exercise unsafe CI artifact path handling. |
| L08 | Compare normalized stealth telemetry with normal detection. |
| L09 | Submit evidence from every previous stage as the capstone chain. |

The route is `http://127.0.0.1:5060`. The challenge service does not unlock stages; it only provides the synthetic discovery condition. The Training Gate remains the sole authority for progression.

## Research-backed AI/APT range

The dated threat model in `training-config/threat-model.json` maps current public reporting to synthetic stages, detection rules, and safe controls. The offline planner renders a nine-phase campaign packet without executing commands or contacting external systems:

```bash
python3 scripts/zodiac_bank_threats.py --validate-only
python3 scripts/zodiac_bank_threats.py --format json --output logs/ai-apt-campaign.json
```

Use [`docs/ai-threat-research-2026.md`](ai-threat-research-2026.md) for the research synthesis, limitations, and source list. Reddit and X are recorded as community signals only; they never establish attribution or prevalence. The campaign focuses on agentic chaining, MCP tool drift, RAG/memory poisoning, identity abuse, post-compromise discovery, adaptive evasion, and defensive containment. Every event is synthetic and side-effect-free.

## Hard scenario range

The strict challenge surface is defined by `training-config/scenarios.json`. It contains **45 scenarios** across the 10 stages, including:

- web-based indirect injection, operationalized indirect-injection feeds, and browser-origin confusion;
- multimodal, split, hidden, and obfuscated prompt content;
- vector tenant confusion, embedding inversion canaries, federation conflict, and context-window exfiltration;
- MCP tool poisoning, tool shadowing, rug pulls, argument confusion, and agent-pivot lateral movement;
- persistent memory poisoning, feedback-summary poisoning, summary provenance, and tenant isolation;
- non-human identity, OAuth scope mismatch, device-code phishing, token-in-logs, refresh-token theft, synthetic deepfake claims, and approval binding;
- CI artifact paths, dependency confusion, slopsquatting, model confusion, model-card tampering, dataset poisoning, model/dependency drift, and workflow metadata injection;
- detector normalization gaps, LLM-assisted evasion, low-and-slow beaconing, unbounded fan-out, model-abuse budgets, egress policy, and privacy-safe canaries;
- campaign correlation, containment/recovery, transfer evaluation, residual risk, and detection-debt lessons learned.

### Evidence is per-run and never shipped in the repository

Every scenario step declares **evidence value types** (for example `http-method`, `scope-route`, `entity-id`, `decision`) instead of literal answers. When a learner starts a scenario, the service:

1. issues a fresh per-run nonce;
2. derives each step's expected values with an HMAC over the flag secret, learner ID, scenario ID, step, key, and nonce;
3. exposes the correct value among distractors from a bounded vocabulary in the step hint;
4. issues a chained step token after each accepted step, which the next step requires as `proof`;
5. counts failed attempts (20 per step) and forces a reset to a fresh run when exhausted.

Reading `training-config/scenarios.json` or another learner's solution therefore never yields a usable answer: values differ per learner, per run, and per step, and later steps cannot be reached without the chained token from the previous step. Wrong order, replay, unknown events, and incomplete evidence are rejected. Completing scenarios is not enough: stage synthesis must include the exact required scenario set, evidence tokens, detection-rule coverage, controls, timeline entries, and a security explanation. In strict mode, the legacy one-request routes never issue hard flags.

List the current stage's scenarios after enrollment:

```bash
curl --fail 'http://127.0.0.1:5060/api/scenarios?learner_id=analyst-01' \
  -H "X-Training-Learner-Token: ${LEARNER_TOKEN}"
```

Start and advance a scenario only with the private learner token:

```bash
curl --fail -X POST http://127.0.0.1:5060/api/scenarios/scope-integrity/start \
  -H "X-Training-Learner-Token: ${LEARNER_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{"learner_id":"analyst-01"}'
```

The API deliberately does not expose step matchers or future steps. The step hint returns the current event name, the required evidence keys, and a candidate pool containing the correct per-run value among distractors. A completed scenario returns an opaque evidence token. Stage synthesis is the only strict-mode path that returns that stage's flag after all controls and reasoning requirements pass.

### Browser trainer console

The challenge service also serves a trainer console at `http://127.0.0.1:5060`. It renders the full 45-scenario range map, per-stage status, progressive clues, next-step hints with candidate-chip evidence picking, chained-proof handling, stage synthesis, and one-click hard-flag submission to the Training Gate. The console uses the learner's private token for every request and keeps it in browser local storage on the localhost machine only.

## API examples

Start the core profile:

```bash
RUNTIME=1 ./scripts/start_all.sh
```

Inspect progress:

After the instructor enrolls the learner, keep the returned token private and use it for learner APIs:

```bash
export LEARNER_TOKEN='<token returned by cohort-add>'
curl --fail 'http://127.0.0.1:5050/api/curriculum?learner_id=analyst-01' \
  -H "X-Training-Learner-Token: ${LEARNER_TOKEN}"
```

Submit a discovered hard flag:

```bash
curl --fail http://127.0.0.1:5050/api/flags/submit \
  -H "X-Training-Learner-Token: ${LEARNER_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{"learner_id":"analyst-01","stage_id":"L00-foundation","flag":"<discovered-flag>"}'
```

A successful response contains the next unlocked stage. Submitting a later-stage flag early is rejected even if the flag is otherwise valid. Open `/api/lessons/{stage_id}` only for the current stage to receive its three progressive hints; locked lessons do not disclose their hints.

## Instructor controls

Set separate private secrets before the first run. Strict mode is enabled by default and refuses the built-in placeholders:

```env
TRAINING_SECURITY_MODE=strict
TRAINING_FLAG_SECRET=<at-least-32-character-random-local-secret>
TRAINING_ADMIN_KEY=<at-least-24-character-separate-instructor-key>
```

For disposable offline unit tests only, `TRAINING_SECURITY_MODE=development` permits defaults; never use that mode for a running training cohort.

Manage cohorts and export progress with the local CLI:

```bash
export TRAINING_ADMIN_KEY=<separate-instructor-admin-key>
python3 scripts/zodiac_bank_admin.py cohort-create cohort-2026 "Zodiac Bank 2026 Cohort"
python3 scripts/zodiac_bank_admin.py cohort-add cohort-2026 analyst-01  # returns a one-time learner token
python3 scripts/zodiac_bank_admin.py completion-report cohort-2026 --format csv --output reports/cohort-2026.csv
python3 scripts/zodiac_bank_admin.py reset-cohort cohort-2026
```

The CLI refuses non-local Training Gate URLs unless `ALLOW_REMOTE_ADMIN=1` is explicitly set. In strict mode, `cohort-add` rotates and returns a per-learner token; send it privately and never commit it to source control. Reset is cohort-scoped and removes submissions/completions only for members of that cohort.

Changing `TRAINING_FLAG_SECRET` invalidates all generated flags. Use `reset-cohort` for a scoped reset; removing the `training_data` volume is the destructive all-cohort reset and deletes all learner progress and generated training artifacts.

Each lesson has a hard scenario surface under `http://127.0.0.1:5060`. In strict mode, legacy one-request routes never issue hard flags. Learners must complete the stage's required multi-step scenarios, preserve ordered evidence tokens, cover detections and controls, and pass stage synthesis before submitting the returned HMAC-backed flag to the Training Gate. Scenario state is persistent and token-bound; responses are synthetic training findings and must not be cached or copied outside the lab.

## Hint progression

In strict mode, learner progress and flag submission also require the instructor-issued `X-Training-Learner-Token`; a learner ID alone is not an identity proof. Hints are intentionally short and do not contain flags, secret material, or steps against external systems. Use them in order:

- **Hint 1 — direction:** identifies the local surface or observation to begin with.
- **Hint 2 — technique:** narrows the comparison or trust boundary to test.
- **Hint 3 — confirmation:** describes the safe synthetic condition that should confirm the finding.

Hints are released only after the stage is unlocked. The next difficulty is not released by reading a hint; it requires valid scenario evidence and an accepted stage synthesis before the hard flag can be submitted.

## Flag progression verification

`scripts/zodiac_bank_progression_test.py` walks the complete 10-stage flag chain through the real service code (FastAPI stubbed, real SQLite state, real HMAC secrets): enroll, solve every required scenario per stage via the trainer candidate pools, synthesize each stage, submit the hard flag to the gate, and assert the exact next stage unlocks through L09 and curriculum completion. It also exercises the negative paths (wrong evidence, tampered chained proof, invalid flag, locked-stage flag, idempotent re-submission). The offline evaluator runs the same journey as the `flag_progression_e2e` regression check:

```bash
python3 scripts/zodiac_bank_progression_test.py
python3 scripts/zodiac_bank_eval.py
```

## Security boundary

- Keep every service bound to `127.0.0.1`.
- Use synthetic identities such as `ZB-CUS-001` and `analyst-01` only.
- Do not use real credentials, customer records, production prompts, or external targets.
- Do not turn a lab finding into an action against a real bank or third-party system.
- At L09, produce a report and containment plan; do not perform destructive actions.
