# Phase 5 — Agentic 2026 Control Range

This phase implements the remaining executable capabilities identified in the
2026 threat-landscape gap analysis. It covers eight research-backed areas that
were previously only documented or partially simulated, while preserving the
existing 100-scenario / 50-hard-gate curriculum and the Phase 1–4 controls.

All Phase 5 modules are **deterministic, local-only, and side-effect-free**. They
model the *decision chains* of real attack classes without issuing network
requests, executing commands, calling a model, or touching a real telemetry
endpoint.

## Research basis

- **CSA — MITRE ATT&CK and ATLAS Agentic Gap Analysis (2026-03-27)** identifies six
  control-plane techniques absent from ATLAS: agent-to-agent lateral movement,
  tool-chain poisoning, orchestrator hijacking, credential relay through delegation
  chains, cross-session memory persistence, and MCP server compromise as a pivot.
- **CSA / Tenet Security — Agentjacking (2026-06-12)** documents MCP telemetry
  injection via a public write-only DSN, with an 85% exploitation rate across
  tested coding agents.
- **OWASP Top 10 for Agentic Applications 2026** frames identity abuse, insecure
  inter-agent communication, and rogue agents as top-tier risks.
- **NVD — CVE-2026-25253 / CVE-2026-24763** describe OpenClaw auth-token theft and
  command-injection RCE.
- **BCG — Agentic AI Will Industrialize Financial Scams (2026-06-11)** projects a
  90% cost reduction and volume surge in scam and deepfake fraud.
- **OpenTelemetry — GenAI Semantic Conventions (2026)** standardize agent, tool,
  and LLM spans and security events.
- **CSA — Image-Based Prompt Injection in Multimodal LLMs (2026-03-08)** covers
  hidden text, OCR injection, and typographic obfuscation.

## New modules

| Module | Gap area |
| --- | --- |
| `scripts/zodiac_control_plane.py` | Agent-to-agent lateral movement, orchestrator hijacking, delegation relay, cross-session memory, MCP pivot |
| `scripts/zodiac_agentjacking.py` | MCP telemetry / DSN injection decision chain |
| `scripts/zodiac_nhi.py` | Non-human identity lifecycle and delegation relay |
| `scripts/zodiac_multimodal.py` | Hidden multimodal and typographic injection |
| `scripts/zodiac_otel.py` | OpenTelemetry GenAI trace spans and correlation |
| `scripts/zodiac_supply_chain_runtime.py` | Digest drift, registry squatting, rug-pull |
| `scripts/zodiac_fraud_agentic.py` | Deepfake bypass, scam orchestration, mule hubs |
| `scripts/zodiac_evolutionary_eval.py` | Mutation and transfer-based adversarial evaluation |

## Threat-model and detection additions

The threat model grows from 15 to **23 threats** (`ZBT-16` … `ZBT-23`), and the
detection ruleset from 11 to **19 rules** (`ZB-AI-011` … `ZB-AI-018`), each mapped
to MITRE ATT&CK / ATLAS and OWASP Agentic categories.

| Threat | Detection | Focus |
| --- | --- | --- |
| ZBT-16 | ZB-AI-011, ZB-AI-013 | Control-plane lateral movement / orchestrator hijack |
| ZBT-17 | ZB-AI-012, ZB-AI-016 | Agentjacking via MCP telemetry injection |
| ZBT-18 | ZB-AI-013, ZB-AI-004 | NHI lifecycle and delegation relay |
| ZBT-19 | ZB-AI-014, ZB-AI-001 | Multimodal and vision injection |
| ZBT-20 | ZB-AI-015, ZB-AI-010 | GenAI trace-correlation gap |
| ZBT-21 | ZB-AI-016, ZB-AI-002 | Runtime supply chain and registry squatting |
| ZBT-22 | ZB-AI-017, ZB-FRAUD-001 | Deepfake and agentic fraud orchestration |
| ZBT-23 | ZB-AI-018, ZB-AI-006 | Evolutionary / mutation-based evaluation |

## Validation

Run the focused Phase 5 regression suite:

```bash
PYTHONPATH=scripts python3 scripts/zodiac_phase5_test.py
```

Run the full offline evaluator (includes the new `phase5_agentic_2026` check):

```bash
python3 scripts/zodiac_bank_eval.py
```

## Safety boundary

- No network access, no shell interpretation, no command execution.
- No real telemetry endpoint, model, or identity provider is contacted.
- All payloads are harmless, clearly-labeled synthetic markers.
- Every hardened path returns a `deny`/`review`/`block` decision plus bounded
  evidence; none of these modules performs an external side effect.
