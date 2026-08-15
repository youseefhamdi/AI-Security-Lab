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
| L04 | Advanced | A2A/MCP confused deputy | A2A Router, Knowledge Agent, MCP |
| L05 | Advanced | Memory poisoning and tenant isolation | Mem0, Aurora, MCP memory |
| L06 | Expert | Identity and AI control plane | Kong, Aurora, A2A, Mem0 |
| L07 | Expert | Model, dependency, and CI supply chain | Dependency Sweeper, Orchestrator, Filebeat |
| L08 | Red Team | Evasion versus SIEM detection | Aurora, Elasticsearch, Kibana, Filebeat |
| L09 | APT Simulation | Multi-stage campaign and containment | All local services |

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

Each lesson has a distinct challenge surface under `http://127.0.0.1:5060`. The challenge service computes the same HMAC-backed flag as the Training Gate but returns it only after the lesson-specific discovery condition is met. Responses are synthetic training findings; they must not be cached or copied outside the lab. The service contains no real banking information.

## Hint progression

In strict mode, learner progress and flag submission also require the instructor-issued `X-Training-Learner-Token`; a learner ID alone is not an identity proof. Hints are intentionally short and do not contain flags, secret material, or steps against external systems. Use them in order:

- **Hint 1 — direction:** identifies the local surface or observation to begin with.
- **Hint 2 — technique:** narrows the comparison or trust boundary to test.
- **Hint 3 — confirmation:** describes the safe synthetic condition that should confirm the finding.

Hints are released only after the stage is unlocked. The next difficulty is not released by reading a hint; it requires a valid hard-flag submission.

## Security boundary

- Keep every service bound to `127.0.0.1`.
- Use synthetic identities such as `ZB-CUS-001` and `analyst-01` only.
- Do not use real credentials, customer records, production prompts, or external targets.
- Do not turn a lab finding into an action against a real bank or third-party system.
- At L09, produce a report and containment plan; do not perform destructive actions.
