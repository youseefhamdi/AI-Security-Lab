# Zodiac Bank AI Platform Architecture

> All hosts, services, credentials, and data in this document are synthetic training fixtures.

## Components

- API Gateway: Kong 3.9 at the local Zodiac Bank gateway
- Support Assistant: Aurora on the local support interface
- Knowledge Agent: A2A retrieval worker for policy and account references
- Memory Layer: Mem0 scoped by customer ID, run ID, and worker ID
- Vector Store: ChromaDB collection `zodiac_bank_docs`
- Graph RAG: LightRAG workspace `zodiac-bank`
- Detection: Elasticsearch, Kibana, and Filebeat

## Domain boundaries

- Staff IDs use the `ZB-STF-*` format.
- Customer IDs use the `ZB-CUS-*` format.
- Branch IDs use the `ZB-BR-*` format.
- Account IDs use the `ZB-ACCT-*` format.
- Case IDs use the `ZB-CASE-*` format.

Canonical examples include customer `ZB-CUS-001` at branch `ZB-BR-001`, account `ZB-ACCT-1001`, and fraud analyst worker `fraud-analyst`.

## Security boundary

This is an intentionally vulnerable local training system. Do not connect it to real bank networks, customer records, credentials, payment rails, or external targets.
