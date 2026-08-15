<div align="center">

<a href="https://github.com/youseefhamdi/AI-Security-Lab">
  <img src="docs/assets/ai-security-lab-banner.svg" alt="AI Security Lab — Recon, Attack, Observe, Detect" width="100%" />
</a>

[![Docker Compose](https://img.shields.io/badge/Docker-Compose%20v2-2496ED?logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![Model](https://img.shields.io/badge/Model-PrismML%20Bonsai%2027B-7C3AED)](https://huggingface.co/prism-ml/bonsai-27b)
[![Inference](https://img.shields.io/badge/Inference-llama.cpp-111827)](https://github.com/ggml-org/llama.cpp)
[![Security Lab](https://img.shields.io/badge/Purpose-AI%20Security%20Training-EF4444)](#-security-boundary)
[![Build Policy](https://img.shields.io/badge/VPS-Build--only-F59E0B)](#-vps-build-only-policy)

> **An isolated AI security lab for practicing model fingerprinting, prompt injection, RAG attacks, agent protocol abuse, memory poisoning, supply-chain scenarios, and SIEM detection.**

</div>

---

## ✨ What is this?

**AI Red Team Lab** is a local-first training environment based on OffSec AI-300 Module 2 concepts. It combines vulnerable AI applications, agent protocols, retrieval systems, memory services, an API gateway, and detection tooling into one reproducible lab.

The project is intentionally unsafe by design. It includes debug leaks, exposed tool schemas, synthetic credentials, prompt-injection weaknesses, unauthenticated protocol exercises, and honeypot data. Run it only on a machine you control and keep all services bound to localhost.

## 🧭 Architecture at a glance

```text
┌──────────────────────────────────────────────────────────────────────┐
│                         AI SECURITY LAB                              │
├──────────────────────────────────────────────────────────────────────┤
│  CORE                                                               │
│  Bonsai 27B / llama.cpp → Aurora · Phoenix · Assistant              │
│  Local Markdown retrieval                                            │
├──────────────────────────────────────────────────────────────────────┤
│  PROTOCOLS                    DATA + MEMORY                          │
│  A2A Router · Knowledge Agent  ChromaDB · Milvus · LightRAG · Mem0   │
│  MCP Server · MCP Wrapper      Optional full-profile services        │
├──────────────────────────────────────────────────────────────────────┤
│  CONTROL + VISIBILITY                                               │
│  Kong API Gateway · Elasticsearch · Kibana · Filebeat                │
│  Agent Orchestrator · Loop Engineering · Understand-Anything        │
└──────────────────────────────────────────────────────────────────────┘
```

## 🎯 Training coverage

| Area | Lab components |
| --- | --- |
| Model reconnaissance | Identity, contradiction, cutoff, capability, context, arithmetic probes |
| Prompt security | Aurora injection, system-prompt extraction, guardrail bypass |
| RAG security | Document enumeration, chunk probing, similarity-threshold testing |
| Agent protocols | A2A Agent Cards, trust mapping, MCP schema discovery and invocation |
| Memory security | Mem0 poisoning, extraction, persistence, cross-user isolation |
| Codebase intelligence | Understand-Anything graph manipulation and architecture leakage |
| Detection engineering | Filebeat, Elasticsearch, Kibana, E01–E05 and D02–D03 rules |
| Orchestration | Task injection, worker impersonation, loop and CI/CD poisoning |

## ⚡ Resource profiles

### `core` — default and smallest footprint

Starts only four containers:

- Bonsai llama.cpp
- Aurora support chatbot
- Phoenix code reviewer
- Assistant OpenAI-compatible API

Aurora uses dependency-free local Markdown retrieval, so the core does not require ChromaDB, Milvus, LightRAG, Mem0, A2A, MCP, Kong, or ELK.

| Resource | Minimum | Recommended |
| --- | ---: | ---: |
| CPU | 4 cores | 6–8 cores |
| RAM | 8 GB | 10–12 GB |
| SSD | 15 GB free | 25 GB free |

### `lite` — core + protocol exercises

Adds the A2A Router, Knowledge Agent, MCP server, and MCP wrapper.

| Resource | Minimum | Recommended |
| --- | ---: | ---: |
| CPU | 4 cores | 8 cores |
| RAM | 10 GB | 12–16 GB |
| SSD | 20 GB free | 30 GB free |

### `full` — complete lab stack

Adds Kong, ChromaDB, Milvus, LightRAG, Mem0, extra MCP servers, Elasticsearch, Kibana, and Filebeat.

- 32 GB RAM minimum
- 48 GB recommended
- 100 GB disk minimum
- 12+ CPU cores recommended

> **Bonsai serving defaults:** 2K context, one concurrent request, CPU mode, and a 5 GB container memory limit. Increase `BONSAI_CONTEXT_SIZE` only when the host has additional memory.

## 🌳 Model setup — no model pulls

The lab uses the already-downloaded **PrismML Bonsai 27B** GGUF, approximately 4 GB:

```text
models/bonsai-27b.gguf
```

If the downloaded filename differs, set it in a local `.env` file:

```env
BONSAI_MODEL_FILE=your-actual-bonsai-file.gguf
```

The project does **not** pull models. Verify the local file without downloading anything:

```bash
./scripts/pull_models.sh
```

Bonsai is served once through llama.cpp's OpenAI-compatible API. All three applications and the A2A agents reuse that same backend.

## 🚀 Quick start

> Run these commands on your local machine—not on the build-only VPS.

### Core mode

```bash
docker compose up -d
```

Or use the guarded startup helper:

```bash
RUNTIME=1 ./scripts/start_all.sh
```

### Lite protocol mode

```bash
RUNTIME=1 LAB_MODE=lite ./scripts/start_all.sh
```

### Full mode

Use this only on a sufficiently provisioned machine:

```bash
RUNTIME=1 LAB_MODE=full SEED_DATA=1 ./scripts/start_all.sh
```

### Inference smoke test

```bash
RUNTIME=1 ./scripts/test_inference.sh
```

### Stop and clean local services

```bash
RUNTIME=1 ./scripts/stop_all.sh
RUNTIME=1 CONFIRM_CLEAN=1 ./scripts/clean_all.sh
```

## 🔌 Endpoints

### Core services

| Service | Endpoint |
| --- | --- |
| Bonsai llama.cpp | `http://127.0.0.1:11435` |
| Aurora | `http://127.0.0.1:5000` |
| Phoenix | `http://127.0.0.1:5001` |
| Assistant | `http://127.0.0.1:5002` |

### Protocol services — `lite` / `full`

| Service | Endpoint |
| --- | --- |
| MCP server | `http://127.0.0.1:3000` |
| MCP wrapper | `http://127.0.0.1:3001` |
| A2A Knowledge Agent | `http://127.0.0.1:5011` |
| A2A Router | `http://127.0.0.1:5010` |
| Legacy A2A agent | `http://127.0.0.1:4000` |
| MCP filesystem | `http://127.0.0.1:3002` |
| MCP fetch | `http://127.0.0.1:3003` |
| MCP memory | `http://127.0.0.1:3004` |

### Full data, gateway, and SIEM services

| Service | Endpoint |
| --- | --- |
| Kong proxy | `http://127.0.0.1:8000` |
| Kong Admin API | `http://127.0.0.1:8001` |
| ChromaDB | `http://127.0.0.1:8010` |
| Mem0 REST API | `http://127.0.0.1:8888` |
| Elasticsearch | `http://127.0.0.1:9200` |
| Milvus | `http://127.0.0.1:19530` |
| LightRAG | `http://127.0.0.1:9621` |
| Redis | `http://127.0.0.1:6379` |
| Kibana | `http://127.0.0.1:5601` |

## 🧱 Project map

```text
apps/                  Aurora, Phoenix, and Assistant
mcp-server/            Deliberately vulnerable MCP server
mcp-wrapper/           HTTP wrapper for MCP tools
a2a-agents/            A2A Router and Knowledge Agent
rag-docs/              Synthetic NovaTech knowledge corpus
sensitive-data/        Honeypot credentials and internal fixtures
exercises/             Recon, attack, evasion, and fingerprint exercises
scripts/               Startup, seeding, detection, and verification helpers
docs/                  Security notes and attack-surface guides
orchestrator-config/   Agent Orchestrator project manifest
mem0-config/           Optional Mem0 configuration
models/                Local GGUF files; ignored by Git
```

## 🔐 VPS build-only policy

The VPS is for building and static verification only:

- Do **not** run `docker compose up` or `docker run`.
- Do **not** pull models.
- Do **not** install OS packages.
- Do **not** contact runtime services.
- Use `bash -n`, `py_compile`, file checks, and `docker compose config` only.
- Transfer the repository and model file to the local machine before execution.

## ⚠️ Mem0 and embedding caveat

The official `mem0/mem0-api-server` image is optional and authenticated by default. Its stock REST image does not bundle every provider needed for a fully local Bonsai-plus-embeddings deployment. The core and lite modes do not start Mem0. Full mode requires a locally customized Mem0 image and a compatible embedding provider for LightRAG/Mem0.

## 🔗 Authoritative upstreams

- [Agent Orchestrator](https://github.com/Untrivial-ai/agent-orchestrator)
- [Mem0](https://github.com/mem0ai/mem0)
- [Loop Engineering](https://github.com/cobusgreyling/loop-engineering)
- [Milvus](https://github.com/milvus-io/milvus)
- [LightRAG](https://github.com/HKUDS/LightRAG)
- [llama.cpp](https://github.com/ggml-org/llama.cpp)

## 🛡️ Security boundary

This project is for **authorized local training only**. It intentionally contains:

- Vulnerable debug and metadata endpoints
- Synthetic credentials and honeypot secrets
- Unauthenticated protocol exercises
- Prompt-injection and guardrail weaknesses
- Memory and retrieval attack fixtures

Never expose the lab to the public internet, reuse its credentials, place real secrets in `sensitive-data/`, or commit model weights.

<div align="center">

### Built for controlled AI security research

`Recon → Exploit → Detect → Harden`

</div>
