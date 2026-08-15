# AI Red Team Lab

A local, deliberately vulnerable lab for practicing OffSec AI-300 Module 2 reconnaissance, protocol discovery, RAG exposure, agent orchestration, and SIEM analysis.

## VPS-aware operating model

This repository is **build-only on the VPS**:

- Do not run `docker compose up` or `docker run` on the VPS.
- Do not pull models or install OS packages on the VPS.
- Use static checks only: shell syntax, Python compilation, file existence, and Compose parsing.
- Run the stack on the local machine after transferring the repository and pre-pulled model assets.

Models are intentionally not pulled by this project. Provide the existing local Ollama model volume and/or `models/bonsai-27b.gguf` separately. The Mem0 OSS API server keeps authentication enabled and uses the lab-only `MEM0_ADMIN_API_KEY` fallback unless you provide a stronger local secret.

## Authoritative upstream projects

| Component | Upstream repository |
| --- | --- |
| Agent Orchestrator | https://github.com/Untrivial-ai/agent-orchestrator |
| Mem0 | https://github.com/mem0ai/mem0 |
| Loop Engineering | https://github.com/cobusgreyling/loop-engineering |
| Milvus | https://github.com/milvus-io/milvus |
| LightRAG | https://github.com/HKUDS/LightRAG |

## Nine-layer architecture

```text
┌─────────────────────────────────────────────────────────────┐
│ 9. Kibana + Filebeat          SIEM UI and log shipping       │
│ 8. Elasticsearch              SIEM storage                  │
│ 7. Red-team fixtures          Honeypots and exercises        │
│ 6. A2A orchestration          Router and knowledge agent     │
│ 5. Applications                Aurora, Phoenix, Assistant    │
│ 4. Protocol servers            MCP, MCP wrapper, A2A         │
│ 3. Data services               ChromaDB and Redis             │
│ 2. Inference                   Existing Ollama + Bonsai       │
│ 1. API gateway                 Kong + PostgreSQL              │
└─────────────────────────────────────────────────────────────┘
```

## Requirements on the local machine

- Docker Engine and Docker Compose v2
- 32GB RAM recommended
- 100GB available disk space
- Pre-pulled local AI model assets

## Quick start on the local machine

Transfer or rsync this repository and model assets to the local machine first. Then, from the project root:

```bash
# Configure Kong after the stack is running.
docker compose up -d
./scripts/configure_kong.sh
```

No model-pull command is included in the Compose architecture. Use the model already installed on the local device.

### Mem0 upstream caveat

The official `mem0/mem0-api-server` image is the real Mem0 REST server and is authenticated by default. Its current bundled server providers do not include Ollama, even though the upstream Mem0 OSS SDK supports Ollama. `mem0-config/config.yaml` therefore records the SDK-compatible Ollama/Chroma configuration; using that configuration in the REST server requires a local image built from the upstream server with the Ollama provider dependencies enabled. Do not assume the environment variables alone enable Ollama in the stock image.

## Endpoints

| Component | Local endpoint |
| --- | --- |
| Kong proxy | http://127.0.0.1:8000 |
| Kong Admin API | http://127.0.0.1:8001 |
| Ollama | http://127.0.0.1:11434 |
| Bonsai llama.cpp | http://127.0.0.1:11435 |
| Milvus | http://127.0.0.1:19530 |
| ChromaDB | http://127.0.0.1:8010 |
| LightRAG | http://127.0.0.1:9621 |
| Mem0 REST API | http://127.0.0.1:8888 |
| Redis | http://127.0.0.1:6379 |
| MCP server | http://127.0.0.1:3000 |
| MCP wrapper | http://127.0.0.1:3001 |
| MCP filesystem | http://127.0.0.1:3002 |
| MCP fetch | http://127.0.0.1:3003 |
| MCP memory | http://127.0.0.1:3004 |
| A2A legacy agent | http://127.0.0.1:4000 |
| A2A router | http://127.0.0.1:5010 |
| A2A knowledge agent | http://127.0.0.1:5011 |
| Aurora | http://127.0.0.1:5000 |
| Phoenix | http://127.0.0.1:5001 |
| Assistant | http://127.0.0.1:5002 |
| Elasticsearch | http://127.0.0.1:9200 |
| Kibana | http://127.0.0.1:5601 |

## Security notes

This is an intentionally vulnerable, localhost-bound training environment. It contains deliberate schema exposure, debug leaks, honeypot data, and unsafe protocol behaviors for authorized lab practice only. Do not expose it to an untrusted network, reuse lab credentials, or commit real secrets/model weights.
