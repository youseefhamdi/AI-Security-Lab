# Synthetic AI/APT Campaign Exercise

This exercise is a defender-focused replay of a research-backed AI-orchestrated campaign. It is **not** a penetration-testing recipe. Run it only against the local Zodiac Bank lab and use synthetic IDs.

## Safety contract

- No external URLs, public targets, real accounts, or real credentials.
- No shell, code, package, model, or network execution is produced by this exercise.
- Every event is metadata or a bounded local challenge condition.
- Side effects are forbidden; containment is a plan, not an action.

## Prepare

```bash
python3 scripts/validate_zodiac_bank.py
python3 scripts/zodiac_bank_threats.py --validate-only
python3 scripts/zodiac_bank_eval.py
```

Generate the campaign packet:

```bash
python3 scripts/zodiac_bank_threats.py \
  --campaign ai-apt-campaign \
  --format json \
  --output logs/ai-apt-campaign.json
```

## Replay the safe local evidence path

With the core profile running and the learner enrolled, list the current stage's scenarios:

```bash
curl --fail 'http://127.0.0.1:5060/api/scenarios?learner_id=analyst-01' \\
  -H "X-Training-Learner-Token: ${LEARNER_TOKEN}"
```

Start each required scenario, discover its observations from the local challenge surfaces, and submit each accepted event in order. The API intentionally rejects wrong order, replay, unknown events, incomplete evidence, and scenarios from future stages. Use synthetic values only:

1. Complete the two required L00-L03 scenarios per stage and capture scope, model, prompt, web, vector, and provenance evidence.
2. At L04, compare the synthetic tool route, complete tool-manifest drift checks, and review argument boundaries without execution.
3. At L05, test only canonical IDs such as `ZB-CUS-001` and `ZB-CUS-002`, including persistence and rollback evidence.
4. At L06, record identity, OAuth scope, nonce, and approval-binding evidence.
5. At L07, review artifact, model-manifest, and workflow metadata behavior without installing or executing anything.
6. At L08, compare normal and encoded marker telemetry, bounded fan-out, canary handling, and transfer results.
7. At L09, correlate the required scenario tokens, submit exact detection and control coverage, and produce the incident timeline, containment, recovery, and residual-risk synthesis.

The goal is to explain the chain and its controls, not to maximize access.

## Required analyst output

For each phase, record:

- phase ID and synthetic event;
- affected Zodiac Bank stage;
- packet ID or workflow run ID;
- source and trust class;
- expected detection rule IDs;
- whether a human approval checkpoint was reached;
- containment and recovery recommendation;
- residual risk and one hardening action.

## Instructor grading rubric

| Area | Pass condition |
| --- | --- |
| Scope | Every request and identifier stays localhost-only and synthetic. |
| Chain reasoning | Learner explains how prompt, tool, identity, memory, and loop risks compose. |
| Detection | Learner identifies expected rules and records a detection gap when normalization differs. |
| Provenance | Learner distinguishes canonical graph evidence from retrieved or user-supplied data. |
| Governance | Learner stops at required approvals and respects step/retry budgets. |
| Response | Learner produces a timeline, quarantine plan, identity-rotation plan, and recovery verification. |

A successful capstone is a defensible incident report with reproducible local evidence and no real-world side effect.
