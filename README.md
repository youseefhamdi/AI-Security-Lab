# AI Red Team Lab

A local, deliberately vulnerable lab for practicing OffSec AI-300 Module 2 reconnaissance, protocol discovery, RAG exposure, agent orchestration, and SIEM analysis.

## VPS-aware operating model

This repository is **build-only on the VPS**:

- Do not run `docker compose up` or `docker run` on the VPS.
- Do not pull models or install OS packages on the VPS.
- Use static checks only: shell syntax, Python compilation, file existence, and Compose parsing.
- Run the stack on the local machine after transferring the repository and pre-pulled model assets.

Models are intentionally not pulled by this project. The core lab uses the already-downloaded PrismML Bonsai 27B GGUF, approximately 4 GB. Set `BONSAI_MODEL_FILE` if its filename differs from `models/bonsai-27b.gguf`. The Mem0 OSS API server is optional and keeps authentication enabled.

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
│ 3. Data services               Optional Chroma/Milvus/RAG    │
│ 2. Inference                   One Bonsai llama.cpp server    │
│ 1. API gateway                 Kong + PostgreSQL              │
└─────────────────────────────────────────────────────────────┘
```

## Resource profiles

### Core mode (default)

The default runtime starts only one Bonsai server, Aurora, Phoenix, and Assistant. Retrieval is dependency-free local Markdown search.

- 4 CPU cores; 6–8 cores recommended
- 8 GB RAM minimum; 10–12 GB recommended
- 15 GB free SSD minimum; 25 GB recommended
- One approximately 4 GB `Bonsai 27B` GGUF file

### Lite mode

Lite mode adds the A2A Router, Knowledge Agent, MCP server, and MCP wrapper.

- 4+ CPU cores; 8 cores recommended
- 10 GB RAM minimum; 12–16 GB recommended
- 20 GB free SSD minimum; 30 GB recommended

Bonsai is configured for a 2K context and one concurrent request by default. Increase `BONSAI_CONTEXT_SIZE` only when additional memory is available.

### Full mode

The optional profile adds Kong, ChromaDB, Milvus, LightRAG, Mem0, MCP extras, Elasticsearch, Kibana, and Filebeat.

- 32 GB RAM minimum
- 48 GB recommended
- 100 GB disk minimum
- 12+ CPU cores recommended

### Software

- Docker Engine and Docker Compose v2
- 64-bit Linux or Docker Desktop
- Internet access for container images and application builds; no model download is performed
- Pre-downloaded `Bonsai 27B` GGUF under `models/`

## Quick start on the local machine

Transfer or rsync this repository and model assets to the local machine first. Then, from the project root:

```bash
# Start the minimal core.
docker compose up -d

# Or use the guarded orchestrator.
RUNTIME=1 ./scripts/start_all.sh

# Add A2A/MCP protocol services.
RUNTIME=1 LAB_MODE=lite ./scripts/start_all.sh

# Full stack, only when the machine has sufficient resources.
RUNTIME=1 LAB_MODE=full SEED_DATA=1 ./scripts/start_all.sh
```

No model-pull command is included in the Compose architecture. The core path does not start Ollama, A2A, MCP, or any storage service and does not pull any model; it mounts the local GGUF from `./models/` into llama.cpp. Verify the file with `./scripts/pull_models.sh` before starting.

### Mem0 upstream caveat

The official `mem0/mem0-api-server` image is the real Mem0 REST server and is authenticated by default. Its current bundled server providers do not include Ollama, even though the upstream Mem0 OSS SDK supports Ollama. `mem0-config/config.yaml` therefore records the optional Bonsai/embedding configuration; using it in the REST server requires a locally customized image and an embedding provider. The core and lite modes do not start Mem0.

## Endpoints

| Component | Local endpoint |
| --- | --- |
| Kong proxy | http://127.0.0.1:8000 |
| Kong Admin API | http://127.0.0.1:8001 |
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
