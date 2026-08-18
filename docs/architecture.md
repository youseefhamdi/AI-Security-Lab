# Architecture overview

This is a high-level map for newcomers. It explains the pieces, how they fit,
and where to look when you want to change something.

## The big picture

Zodiac Bank is a **synthetic bank** used as a teaching attack surface. A
learner advances through 10 hard-gated stages (L00–L09) by solving
multi-step scenarios, synthesizing evidence into a hard-gate flag, and
submitting that flag to a progression gate.

```
Synthetic Bank Domain ──► Graph / RAG / Memory ──► Protocol Surfaces
        │                                                   │
        └──────────── Control & Visibility ◄────────────────┘
                     (Gate · Detection · Trainer)
```

Three profiles scale the stack:

| Profile | Services | Use |
| --- | --- | --- |
| `core` | Training Gate, Challenge Surface, Graph Context, Aurora/Phoenix/Assistant, inference | First run |
| `lite` | core + A2A router/knowledge agent + MCP server/wrapper | Protocol attacks |
| `full` | lite + Kong, storage, SIEM (ELK) | Detection and telemetry labs |

## Services

| Service | Host port | Role |
| --- | --- | --- |
| Training Gate (`training-gate/`) | `5050` | Progression authority; issues/validates hard-gate flags |
| Challenge Surface (`training-challenges/`) | `8060` | Scenarios, evidence chains, trainer UI (`/`), solution guides |
| Graph Context (`graph-context/`) | `5070` | Provenance-aware graph + bounded context packets |
| Aurora (`apps/aurora/`) | `5000` | Support chatbot (RAG/context attack surface) |
| Phoenix (`apps/phoenix/`) | `5001` | Code reviewer (prompt-injection surface) |
| Assistant (`apps/assistant/`) | `5002` | OpenAI-compatible API playground |
| Bonsai (`docker-compose.yml`) | `11435` | Local llama.cpp inference fallback |

> The challenge service listens on `5060` *inside* its container but is
> published on host `8060`, because Chromium blocks `5060` (SIP) as an
> unsafe port.

## Data and configuration

| Path | What it holds |
| --- | --- |
| `training-config/` | curriculum, threat model, bank profiles, scenario packs (166 scenarios / 83 gates) |
| `detection-config/` | synthetic Sigma-like detection rules |
| `bank-data/` | branches, employees, customers, accounts, operations |
| `rag-docs/` | synthetic knowledge corpus (untrusted retrieval content) |
| `sensitive-data/` | honeypot credentials and fixtures |
| `scripts/` | startup, evaluation, threat modeling, progression, Phase 5 controls |

Scenario packs are compiled by `scripts/zodiac_scenario_engine.py`. Evidence
values are **derived per-run** from the flag secret + a fresh nonce — the
repository contains no literal answers.

## The learner journey

```
Enroll (cohort + token)
   └─► Solve required scenarios (evidence + chained proof)
         └─► Synthesize hard gate (evidence tokens, detections, controls,
             timeline, concepts)
               └─► Submit flag to Training Gate
                     └─► Next stage unlocks
                           └─► … through L09 (APT capstone)
```

Each accepted flag promotes a persistent learner profile: stricter controls,
narrower synthetic data scope, smaller agent budgets, and a new active
surface.

## Key scripts

| Script | Purpose |
| --- | --- |
| `scripts/start_all.sh` | detect provider, validate, start the selected profile |
| `scripts/zodiac_bank_eval.py` | 11 offline posture/security checks |
| `scripts/zodiac_bank_progression_test.py` | solve all 166 scenarios / 83 gates offline |
| `scripts/zodiac_bank_threats.py` | research-informed threat model validation |
| `scripts/check_ui_types.mjs` | `tsc --checkJs` over every inline UI script |

## Where to start hacking

- **Add a scenario** → `training-config/scenario-expansion-*.json`, then
  `python3 scripts/validate_zodiac_bank.py`.
- **Change the trainer UI** → `training-challenges/index.html` (single file).
- **Add a detection rule** → `detection-config/zodiac-bank-rules.json`.
- **Add a Phase 5 control module** → `scripts/zodiac_*.py` + its test.

See [CONTRIBUTING.md](../CONTRIBUTING.md) for the full workflow.
