# Mem0 Security Attack Surfaces

This document defines authorized lab exercises for testing memory isolation and trust boundaries. The lab data and attack payloads are synthetic.

## Memory injection via untrusted input

An application may automatically write user messages, retrieved documents, tool output, or agent observations into long-lived memory. An attacker can inject instructions that look like facts, such as a false role, permission, preference, or policy. Later prompts may treat the poisoned entry as trusted context.

**Test:** write a clearly false memory through the normal application path, then query it from a later session. Verify that provenance, confidence, source, and consent are retained and that untrusted content is not promoted to durable fact without review.

**Controls:** classify memory writes by source, require explicit user confirmation for durable preferences, attach immutable provenance, sanitize instruction-like content, and support deletion or quarantine of suspicious memories.

## Memory extraction via prompt injection

A prompt can instruct an agent to reveal all stored memories, identifiers, hidden system context, or other users' records. A memory provider should not treat the requesting prompt as authorization to broaden retrieval.

**Test:** use the extraction payload in `exercises/mem0_attacks.sh` and confirm that the response is scoped to the authenticated user, task, and approved fields.

**Controls:** apply retrieval-time authorization, minimize fields returned to the model, redact secrets, enforce output filtering, and log extraction attempts.

## Session hijacking via memory manipulation

If `session_id` is accepted from an untrusted client or can be guessed, an attacker may append to or retrieve another session's memories. Session identifiers must not be the sole authorization boundary.

**Test:** inject under one session and query under a second session while keeping the same user, then repeat with a different user. The second user must receive no data from the first.

**Controls:** bind session records to an authenticated principal, use high-entropy server-issued identifiers, verify ownership on every read/write/delete, and expire session-scoped memory.

## Cross-user memory leakage

Incorrect filters, shared namespaces, broad agent privileges, or vector-search-only retrieval can return another user's preferences or facts. Similar text is not proof of authorization.

**Test:** seed Alice and another synthetic user with distinctive facts. Query each identity using exact and semantic searches, including an `agent_id` that has access to only one tenant.

**Controls:** enforce tenant/user predicates before vector search, separate collections or namespaces where appropriate, test negative authorization cases, and avoid exposing raw metadata identifiers to the model.

## Multi-level memory boundaries

The lab distinguishes:

- `user_id`: durable user facts and preferences.
- `session_id`: short-lived conversation state.
- `agent_id`: facts owned or observed by a particular agent.

Every retrieval should state which levels are allowed. A request for session context must not implicitly return all user or agent memory.

## Detection and response

Record memory writes, reads, deletes, failed authorization checks, unusually broad queries, and prompt-injection patterns. Canary memories should be synthetic and monitored. When poisoning is detected, quarantine affected records, preserve provenance for investigation, rotate any exposed test credentials, and re-run cross-user isolation tests.
