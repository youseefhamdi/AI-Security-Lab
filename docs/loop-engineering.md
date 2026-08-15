# Loop Engineering Patterns

Authoritative upstream: https://github.com/cobusgreyling/loop-engineering

The upstream project provides the unified CLI front door, including `npx @cobusgreyling/loop init`, `doctor`, `audit`, and `cost`. This lab documents the patterns and keeps runtime automation disabled on the VPS.

Loop Engineering runs recurring agent workflows with bounded inputs, explicit state, and review checkpoints. The following patterns are intentionally documented with their failure modes for the lab.

## Daily Triage loop

**Pattern:** collect new issues, classify priority, delegate to workers, summarize status, and create a plan for the next cycle.

**Attack surface:** a malicious issue title, comment, or imported task can inject instructions into the planner. Conflicting worker summaries may cause priority inversion or unauthorized delegation.

**Controls:** treat issue text as untrusted data, use a typed triage schema, cap task scope, require human approval for destructive actions, and retain source links for every classification.

## PR Babysitter loop

**Pattern:** monitor active pull requests, run tests, ask workers to address review comments, and report readiness.

**Attack surface:** concurrent workers can collide on branches or worktrees, overwrite each other's fixes, or review a stale commit. A poisoned review comment can instruct the agent to weaken a guardrail.

**Controls:** lock branch/worktree ownership, pin every run to a commit SHA, use isolated worktrees, rebase or merge explicitly, and require fresh tests after conflict resolution.

## CI Sweeper loop

**Pattern:** inspect failed CI jobs, cluster recurring failures, delegate fixes, and retry bounded pipelines.

**Attack surface:** a failing job can trigger an unbounded retry loop, resource exhaustion, log flooding, or a task injection through generated CI output.

**Controls:** enforce retry budgets, timeouts, concurrency limits, output truncation, cost ceilings, and strict parsing of logs as untrusted data. Require approval before changing workflow permissions.

## Dependency Sweeper loop

**Pattern:** inspect outdated or vulnerable dependencies, propose updates, run tests, and open review changes.

**Attack surface:** malicious package metadata, typosquatting, compromised releases, install hooks, lockfile poisoning, and a worker that disables security checks to obtain a green build.

**Controls:** use trusted registries and lockfiles, verify signatures/provenance, run installs in isolation, block lifecycle scripts where possible, review diffs and transitive changes, and never let the sweeper auto-merge dependency updates.

## Cross-loop safeguards

Every loop should have a durable run ID, authenticated worker identity, bounded input/output, explicit approval gates, independent audit logs, and a kill switch. The loop must fail closed when its context, branch ownership, model provider, or tool authorization is ambiguous.

## Runnable Dependency Sweeper

The lab includes an offline-safe implementation of the Dependency Sweeper pattern in `scripts/dependency_sweeper.py`. It reads normalized JSON findings, persists runs/items/events in SQLite, creates review-only update proposals, recovers expired worker leases, and applies a bounded retry budget. It never installs packages, edits manifests, opens pull requests, or merges changes.

Run the synthetic fixture locally:

```bash
RUNTIME=1 python3 scripts/dependency_sweeper.py
```

Defaults:

- Input: `loop-config/dependency-findings.json`
- State: `logs/dependency-sweeper.sqlite3`
- Summary: `logs/dependency-sweeper-summary.json`
- Retry budget: three retries plus the initial attempt
- Maximum findings per run: 100
- Maximum worker lease: five minutes

Use a real, pre-generated finding file without allowing the workflow to invoke a package manager:

```bash
RUNTIME=1 python3 scripts/dependency_sweeper.py \\
  --input ./dependency-findings.json \\
  --state ./logs/dependency-sweeper.sqlite3 \\
  --max-retries 2 \\
  --retry-delay 1
```

The JSON summary reports bounded proposal records with `requires_human_approval: true` and `auto_merge: false`. Re-running the same finding is idempotent because its normalized content is fingerprinted; changing the finding creates a new review item.

## Zodiac Bank branched workflows

`bank-data/workflows.json` is the canonical Loop Engineering workflow registry. Every workflow declares a trigger, bounded step/retry budget, explicit branch predicates, and a worker route. `orchestrator-config/zodiac-bank.json` is its symmetric delegation projection; `scripts/validate_zodiac_bank.py` rejects missing workers, workflow IDs, or route delegates.

The side-effect-free runner persists queued worker steps and emits a provenance-aware plan:

```bash
RUNTIME=1 python3 scripts/zodiac_bank_workflows.py \\
  --workflow fraud-investigation \\
  --case-id ZB-CASE-002
```

The runner resolves customer risk/KYC data and branch metadata from `bank-data/zodiac-bank.json`, selects the matching branch, caps the route by `max_steps`, and records the plan in `logs/zodiac-bank-workflows.sqlite3`. It does not approve accounts, freeze funds, send messages, or call external systems.
