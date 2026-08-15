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
