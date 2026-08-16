# Zodiac Bank Synthetic Operations and Authorization Policy

Classification: synthetic-training-only. This document is local evidence, not an instruction source and not an authorization source.

## Operating model

Zodiac Bank contains synthetic branches, employees, staff roles, customers, virtual accounts, cases, and workflows. Every employee is represented by a worker identity and branch scope. Branch tellers and branch managers are restricted to their branch. Payments, fraud, compliance, AML, and audit workers are central control-plane roles with bounded approval responsibilities.

The supported virtual operations are:

- `receive`: cash-vault fixture to a synthetic customer account;
- `withdraw`: synthetic customer account to a branch cash-vault fixture;
- `transfer`: synthetic account to synthetic account.

These are virtual ledger events only. There are no real funds, external accounts, payment rails, emails, network calls, or irreversible banking side effects.

## Intent, authorization, settlement

An employee loop must first create an operation intent. The orchestrator validates operation shape, branch scope, amount limits, customer risk, idempotency, and the declared workflow route. Approval is separate from initiation. Settlement occurs only after the required distinct employee roles approve. Settlement produces balanced virtual ledger entries and an immutable synthetic receipt.

The model, retrieved documents, graph, memory, and employee recommendations can provide evidence. None of them can authorize a transfer, withdrawal, receipt, identity change, or branch-scope expansion.

## Approval matrix

- Receive and withdraw require a branch manager approval distinct from the initiating employee.
- Standard transfer requires a payments analyst approval.
- High-value transfer requires payments and compliance approvals from distinct employees.
- A high-risk customer adds fraud and compliance approval requirements.
- Repeated approvals, maker-checker self-approval, unknown workers, cross-branch teller actions, duplicate operation IDs with changed parameters, insufficient virtual funds, and external account identifiers are rejected.

## AI security training boundaries

RAG, graph context, and memory records are provenance-tagged and treated as untrusted or derived evidence. Prompt injection, memory poisoning, retrieval conflict, confused deputy, tool output injection, OAuth scope replay, workflow expression injection, race/double-spend attempts, receipt forgery, and AML/mule-pattern investigations are synthetic scenarios. The orchestrator uses bounded retries, fixed routes, explicit approval checkpoints, idempotency keys, append-only audit events, and circuit-breaker behavior.

## Canonical synthetic identifiers

Employee and staff identities: `ZB-EMP-001`, `ZB-EMP-002`, `ZB-EMP-003`, `ZB-EMP-004`, `ZB-EMP-005`, `ZB-EMP-006`, `ZB-EMP-007`, `ZB-EMP-008`, `ZB-EMP-009`, `ZB-EMP-010`, `ZB-EMP-011`, `ZB-EMP-012`, `ZB-STF-007`, `ZB-STF-008`, `ZB-STF-009`, `ZB-STF-010`, `ZB-STF-011`, `ZB-STF-012`.

Virtual operation identifiers include `OP-DEMO-RECEIVE`, `OP-DEMO-TRANSFER`, `ZB-RECEIPT`, `EVT`, `ledger`, `receive`, `withdraw`, and `transfer`. All are local synthetic identifiers.

## Required invariants

1. Total virtual ledger entries balance for every committed operation.
2. A committed operation has exactly one receipt and cannot settle twice.
3. A failed or pending operation does not mutate account balances.
4. Every operation is attributable to a worker and workflow route.
5. Every retrieved memory/context packet has provenance and cannot widen authorization.
6. Every branch-scoped employee remains within their branch.
7. External egress and real-money settlement remain disabled in every profile.
