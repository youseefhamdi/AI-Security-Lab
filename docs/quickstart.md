# Quickstart — first lab in 5 minutes

This gets you from a fresh clone to the trainer console as fast as possible.
It uses the **core** profile (Training Gate, Challenge Surface, Graph Context,
Aurora/Phoenix/Assistant, and an inference backend).

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) with Docker Compose v2
  (Podman works too — see below)
- `curl`, `git`
- ~8 GB RAM for core mode
- Either a local GGUF model **or** an already-running inference provider
  (Ollama / LM Studio / llama.cpp). The lab never downloads a model.

## One-time setup

```bash
git clone https://github.com/youseefhamdi/AI-Security-Lab.git
cd AI-Security-Lab
make setup
```

`make setup` creates `.env` (from `.env.example`) and generates strong local
secrets for strict security mode.

### Get a model (skip if you use Ollama / LM Studio)

Drop any `.gguf` under `models/` — a smaller model is fine for a first run:

```bash
mkdir -p models
cp /path/to/your-model.gguf models/
```

If you use Ollama or LM Studio, start it first; the lab auto-detects it.

## Start the lab

```bash
make up
```

Or directly:

```bash
RUNTIME=1 ./scripts/start_all.sh
```

The helper prints the selected provider, validates the threat model and
offline security checks, then starts the core services.

## Open the trainer console

```
http://127.0.0.1:8060
```

Log in with a learner ID and token from a cohort you create:

```bash
export TRAINING_ADMIN_KEY='<instructor key from .env>'
python3 scripts/zodiac_bank_admin.py cohort-create cohort-2026 "My Cohort"
python3 scripts/zodiac_bank_admin.py cohort-add cohort-2026 analyst-01
# -> returns the learner token; paste both into the trainer console
```

Complete the current stage's scenarios, synthesize the hard gate, and submit
the flag to unlock the next stage. See
[Zodiac Bank progression](../README.md#-zodiac-bank-progression).

## Verify it's healthy

```bash
curl --fail http://127.0.0.1:8060/health   # challenge surface
curl --fail http://127.0.0.1:5050/health   # progression gate
curl --fail http://127.0.0.1:5000/health   # Aurora
```

## Stop it

```bash
make down
```

## No Docker? Run the checks offline

The full progression can be verified without starting anything:

```bash
make verify
```

## Podman

If `docker` and `docker compose` are Podman wrappers, the same commands work.
For a host-provided inference backend, set
`INFERENCE_CONTAINER_HOST=host.containers.internal` in `.env`.

## Next steps

- [Architecture overview](architecture.md)
- [Full README](../README.md)
- [Contributing](../CONTRIBUTING.md)
