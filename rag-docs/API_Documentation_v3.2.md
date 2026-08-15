# Zodiac Bank Internal AI API v1

> Synthetic training documentation only. No real banking API exists at these paths.

## Authentication and scope

Requests carry a synthetic worker identity and customer scope. A customer scope must use a `ZB-CUS-*` ID, and a branch scope must use a `ZB-BR-*` ID. Similar names do not authorize access.

## Endpoints

- `GET /health` — service health and model metadata
- `POST /api/chat` — synthetic support conversation
- `POST /api/rag/query` — policy retrieval query
- `POST /api/memory/search` — scoped synthetic memory search
- `POST /api/agent/dispatch` — internal training delegation surface
- `GET /api/admin/approval` — synthetic control-plane approval test

## Retrieval fields

Every canonical result should preserve `entity_id`, `entity_type`, `branch_id`, `source`, and `synthetic=true`. A result without provenance must not be treated as an authoritative customer or staff record.

## Rate limits and review

- Standard support queries: bounded local training requests only
- High-risk or critical cases: require compliance and fraud review workers
- Account changes, approvals, and notifications: never auto-approved by the training workflows
