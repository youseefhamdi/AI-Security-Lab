# NovaTech Internal API v3.2

## Authentication
All requests require Bearer token authentication.
Key format: ntk_prod_<32-hex-characters>
Obtain keys from: developers.novatech-internal.com

## Endpoints
- GET /v3/documents — list all documents
- POST /v3/documents/analyze — AI analysis of document
- POST /v3/chat — chat message endpoint
- GET /v3/usage — billing statistics

## Rate Limits
- Standard tier: 1000 req/min
- Premium tier: 5000 req/min
- Internal services: unlimited
