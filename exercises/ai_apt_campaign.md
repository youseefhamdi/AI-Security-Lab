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

With the core profile running and the learner enrolled, progress through the hard-gated stages. Use the challenge surface only with synthetic values:

1. Complete L00-L03 and capture scope, model, prompt, and RAG provenance evidence.
2. At L04, compare the synthetic tool route and the declared allowed tool list.
3. At L05, test only canonical IDs such as `ZB-CUS-001` and `ZB-CUS-002`.
4. At L06, record the denied or suspicious synthetic identity context.
5. At L07, review the artifact path behavior without installing or executing anything.
6. At L08, compare normal and encoded marker telemetry and record the detection gap.
7. At L09, assemble the evidence timeline and submit it to the local capstone.

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
