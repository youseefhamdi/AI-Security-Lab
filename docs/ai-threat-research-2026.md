# Zodiac Bank AI Threat Research and APT Range

**Research snapshot:** 2026-08-15
**Scope:** defensive training against localhost-only synthetic systems
**Status:** research-informed, not a claim of live compromise or attribution

## Why the lab changed

Recent reporting shows a shift from AI used only as a productivity assistant toward AI used inside multi-step cyber operations. The important training lesson is not that an AI model independently replaces an intrusion team. It is that an agent can compress reconnaissance, coding, decision support, tool selection, evidence sorting, and reporting into a bounded number of human checkpoints. That changes what defenders must measure:

- the sequence and speed of actions, not only individual prompts;
- the identity and authorization attached to every tool call;
- changes to tool descriptions, schemas, models, and retrieval sources;
- post-compromise discovery and privilege activity, not only initial phishing;
- memory, graph, and RAG provenance over time;
- volume, fan-out, and egress anomalies;
- human approval checkpoints and the ability to stop a loop.

The lab therefore adds a research-backed threat model, Sigma-like local detections, and a safe synthetic campaign planner. The planner emits evidence and expected telemetry; it does not scan, exploit, execute commands, contact a model, or contact an external system.

## Research synthesis

### 1. Agentic cyber operations and reduced human intervention

Anthropic's November 2025 report describes an AI-orchestrated espionage campaign that used an agent with tools for reconnaissance, vulnerability research, credential harvesting, data classification, and documentation. Anthropic reports that the model performed 80-90% of the campaign with intermittent human intervention. Its June 2026 analysis of 832 banned accounts reports that AI use moved deeper into post-compromise activity, including account discovery and lateral movement, and that agentic scaffolding is a stronger risk signal than the interface used.

**Training response:** `ZBT-03`, `ZBT-04`, and `ZBT-06` model bounded orchestration, post-compromise discovery, identity abuse, fan-out, and high-volume collection as a sequence. The capstone requires scope, identity, collection, and containment approvals.

### 2. AI throughout the attack lifecycle and adaptive behavior

Google Threat Intelligence Group's November 2025 tracker describes AI-assisted activity across reconnaissance, lure generation, infrastructure, command generation, and exfiltration. It also describes experimental or observed malware that queries an LLM during execution to generate or alter behavior, plus social-engineering pretexts such as presenting activity as a CTF or research task.

**Training response:** `ZBT-07` uses equivalent harmless markers, encoding, and normalization differences to teach detector canonicalization. The exercise never generates or executes a command. The curriculum treats a claimed CTF or research context as untrusted metadata, not authorization.

### 3. MCP tool poisoning, shadowing, and rug pulls

Invariant Labs' April 2025 research describes hidden instructions in MCP tool descriptions, cross-tool shadowing, and post-approval tool-description changes. OWASP's February 2026 secure MCP guide emphasizes authentication, authorization, strict validation, session isolation, and hardened deployment. The community discussions on Reddit and X are useful discovery signals, but they are explicitly not treated as prevalence evidence.

**Training response:** `ZBT-02` and rules `ZB-AI-002`/`ZB-AI-007` require pinned manifests, description and schema hashes, explicit re-approval, and server identity binding. The local scenario compares synthetic manifests; it never reads host files or transmits data.

### 4. AI risk management, data poisoning, and provenance

NIST AI 600-1 identifies prompt injection, data poisoning, supply-chain risk, privacy risk, and security monitoring as generative-AI risk areas. MITRE ATLAS provides a living AI-specific adversary knowledge base, while MITRE ATT&CK remains useful for the surrounding enterprise behavior such as valid accounts, discovery, command execution, and exfiltration.

**Training response:** `ZBT-01` and `ZBT-05` preserve trust labels for user input, canonical graph evidence, and retrieved documents. Cross-tenant expansion, missing provenance, and instruction-like evidence are rejected or quarantined. No graph edge or memory record grants authorization.

### 5. Agent governance and secure adoption

CISA and international partners published guidance for careful adoption of agentic AI in 2026. The common defensive pattern across the guidance is to define bounded permissions, observe agent behavior, separate identities, use human review for high-impact actions, and deploy incrementally in lower-risk environments.

**Training response:** the Loop Engineering workflows retain bounded steps and retries, persistent checkpoints, typed workers, and approval requirements. `ZB-AI-010` stops loops that exceed their declared budget or proceed without required approval.

## Threat-to-control matrix

The canonical machine-readable source is:

```text
training-config/threat-model.json
```

Detection rules are in:

```text
detection-config/zodiac-bank-rules.json
```

| Threat | Zodiac Bank stage | Primary control | Primary telemetry |
| --- | --- | --- | --- |
| Direct/indirect prompt injection | L02 | typed trust boundaries | `ZB-AI-001`, `ZB-AI-006` |
| MCP tool poisoning/shadowing/rug pull | L04 | pinned signed manifests | `ZB-AI-002`, `ZB-AI-007` |
| Agentic attack-chain orchestration | L09 | bounded loops and approvals | `ZB-AI-003`, `ZB-AI-010` |
| Post-compromise discovery/identity abuse | L06 | cryptographic identity binding | `ZB-AI-004`, `ZB-AI-008` |
| RAG and memory poisoning | L03/L05 | provenance and tenant scope | `ZB-AI-005`, `ZB-AI-009` |
| High-volume collection/exfiltration | L08 | baselines, egress policy, circuit breaker | `ZB-AI-003`, `ZB-AI-010` |
| Adaptive evasion | L08 | canonicalization and behavioral telemetry | `ZB-AI-006`, `ZB-AI-010` |
| Model/dependency/artifact supply chain | L07 | digest pinning and isolated review | `ZB-AI-002`, `ZB-AI-007` |
| Synthetic identity automation | L01/L06 | independent verification | `ZB-AI-004`, `ZB-AI-008` |
| Model/API abuse and extraction pressure | L01/L08 | request budgets and abuse monitoring | `ZB-AI-003`, `ZB-AI-010` |

## Safe APT campaign

Run the offline campaign planner:

```bash
python3 scripts/zodiac_bank_threats.py
python3 scripts/zodiac_bank_threats.py --format json --output logs/ai-apt-campaign.json
python3 scripts/zodiac_bank_threats.py --validate-only
```

The nine phases are synthetic events only:

1. baseline service inventory and counters;
2. goal-hijack marker in untrusted content;
3. MCP description drift;
4. bounded post-compromise discovery;
5. memory/graph trust-scope violation;
6. identity and approval disagreement;
7. collection-pressure counters;
8. encoded/normalized detector gap;
9. quarantine, pinning, rotation, and incident timeline.

Every phase has expected detection rules, a training stage, an approval requirement where appropriate, and `side_effects: forbidden`. Use the output to practice triage and incident response, not to imitate an attack against real systems.

## Operator and instructor controls

Before a cohort starts:

```bash
python3 scripts/validate_zodiac_bank.py
python3 scripts/zodiac_bank_threats.py --validate-only
python3 scripts/zodiac_bank_eval.py
```

For each simulated incident, require the learner to record:

- packet ID, workflow run ID, or synthetic event ID;
- source and trust class for each piece of evidence;
- authenticated worker identity and requested route;
- the detection rule that fired or failed;
- the approval checkpoint and reviewer decision;
- containment, recovery, and residual-risk notes.

Do not record raw credentials, real customer data, model-provider keys, or raw prompt content in SIEM fixtures. Hash or redact sensitive values.

## Research limitations

- Threat reports describe observations from the reporting organizations' visibility; they do not establish universal prevalence.
- Social posts and Reddit discussions are community signals and may contain errors, duplicates, or unverified claims.
- The lab uses synthetic analogues. A challenge flag or local behavior is not evidence that a real vendor, bank, model, or threat actor is vulnerable.
- The current date and source URLs are recorded so instructors can review and refresh the model every 30 days.
- MITRE ATT&CK and ATLAS labels are used for defensive orientation; mappings are intentionally high-level where an AI-specific technique does not have a stable one-to-one identifier.

## Sources

- [Anthropic: AI-orchestrated cyber espionage campaign](https://www.anthropic.com/news/disrupting-AI-espionage)
- [Anthropic: mapping a year's worth of AI-enabled cyber threats](https://www.anthropic.com/news/AI-enabled-cyber-threats-mitre-attack)
- [Anthropic: detecting and countering misuse, August 2025](https://www.anthropic.com/news/detecting-countering-misuse-aug-2025)
- [Google Threat Intelligence Group: AI Threat Tracker](https://cloud.google.com/blog/topics/threat-intelligence/threat-actor-usage-of-ai-tools)
- [CISA: secure adoption of agentic AI](https://www.cisa.gov/news-events/news/cisa-us-and-international-partners-release-guide-secure-adoption-agentic-ai)
- [OWASP: Agentic Security Initiative](https://genai.owasp.org/initiatives/agentic-security-initiative/)
- [OWASP: secure MCP server development](https://genai.owasp.org/resource/a-practical-guide-for-secure-mcp-server-development/)
- [Invariant Labs: MCP tool poisoning](https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks)
- [MITRE ATLAS](https://atlas.mitre.org/)
- [MITRE ATT&CK Enterprise Matrix](https://attack.mitre.org/matrices/enterprise/)
- [NIST AI 600-1 Generative AI Profile](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf)
- [Reddit r/netsec community signal](https://www.reddit.com/r/netsec/comments/1ldiilv/security_analysis_mcp_protocol_vulnerabilities_in/)
- [X community signal](https://x.com/cybersec/article/2026335843628007453)
