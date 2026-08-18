# Contributing to Zodiac Bank AI Security Lab

Thank you for contributing! This project is a **local-only, synthetic training
lab** for AI security. Contributions that keep the lab safe, bounded, and
easy to run are always welcome.

## Ground rules

- **Stay synthetic and localhost-bound.** Never add real credentials, real
  customer/financial data, external egress, or code that contacts live
  services. The lab is intentionally vulnerable *inside its challenge
  surfaces* but must remain a controlled, local training environment.
- **No model weights in the repo.** `models/*.gguf` is gitignored; model files
  are transferred separately.
- **Keep it framework-free.** The four UIs (`training-challenges`,
  `apps/aurora`, `apps/phoenix`, `apps/assistant`) are single-file vanilla
  HTML/CSS/JS — no bundler, no npm, no `package.json`.

## Before you start

1. Search [open issues](https://github.com/youseefhamdi/AI-Security-Lab/issues)
   to avoid duplicating work.
2. For larger changes, open an issue describing the problem and your approach
   before writing code.

## Development workflow

```bash
git clone https://github.com/youseefhamdi/AI-Security-Lab.git
cd AI-Security-Lab
make setup          # copy .env + generate strong local secrets
make up             # start the core lab (detects an inference provider)
```

You don't need a running lab for most changes — the project has a fully
offline test path:

```bash
make verify         # offline security evaluation + progression + UI typecheck
make test           # offline evaluation + scenario validation + progression
```

### What each check covers

| Command | What it verifies |
| --- | --- |
| `python3 scripts/zodiac_bank_eval.py` | 11 offline posture/security checks |
| `python3 scripts/validate_zodiac_bank.py` | canonical bank data + orchestrator symmetry |
| `python3 scripts/zodiac_bank_progression_test.py` | full 166-scenario / 83-gate flag progression |
| `node scripts/check_ui_types.mjs` | TypeScript `--checkJs` on every inline UI script |

### Editing the UIs

The trainer, Aurora, Phoenix, and Assistant UIs are JSDoc-typed
(`// @ts-check`) and verified with the real TypeScript compiler in `checkJs`
mode. After editing any `index.html`:

```bash
node scripts/check_ui_types.mjs
```

### Editing scenarios / curriculum

Scenario packs live in `training-config/`. The engine
(`scripts/zodiac_scenario_engine.py`) validates them. After changing a pack:

```bash
python3 scripts/validate_zodiac_bank.py
python3 scripts/zodiac_bank_eval.py
```

Gate-count validation is dynamic ("one gate per two scenarios"), so adding
scenarios does not require editing hardcoded counts.

## Style

- **Python:** standard library + the dependencies already in the service
  `requirements.txt` files. Match the surrounding style (4-space indent,
  type hints where present).
- **Shell:** POSIX `bash` with `set -euo pipefail`. Run `bash -n` on any
  script you touch.
- **Docs:** keep the README and `docs/*.md` accurate — counts, ports, and
  security-mode behavior must reflect the code.

## Commit messages

Use a short imperative summary explaining *why*, not just *what*. Example:

```
Wire trainer solution studio to real backend data
```

## Reporting security issues

Please do **not** open a public issue for a vulnerability. See
[SECURITY.md](SECURITY.md) for the private reporting path.

## Code of conduct

All participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md).
