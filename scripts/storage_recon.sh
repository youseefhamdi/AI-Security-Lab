#!/usr/bin/env bash
set -euo pipefail

CHROMA_API_URL="${CHROMA_API_URL:-http://127.0.0.1:8010/api/v1}"
CHROMA_COLLECTION_ID="${CHROMA_COLLECTION_ID:-}"
# Official project: https://github.com/milvus-io/milvus
MILVUS_URL="${MILVUS_URL:-http://127.0.0.1:19530}"
MILVUS_COLLECTION="${MILVUS_COLLECTION:-novatech_vectors}"
LIGHTRAG_URL="${LIGHTRAG_URL:-http://127.0.0.1:9621}"
QUERY_EMBEDDING_JSON="${QUERY_EMBEDDING_JSON:-}"
RECON_QUERY="${RECON_QUERY:-vacation policy and internal infrastructure}"
ENTITY="${ENTITY:-NovaTech}"

log() {
  printf '[storage-recon] %s\n' "$*"
}

fail() {
  printf '[storage-recon] ERROR: %s\n' "$*" >&2
  exit 1
}

if [[ "${RUNTIME:-0}" != "1" ]]; then
  log "Static/VPS mode: RUNTIME is not 1; no storage queries will run"
  log "Local execution requires QUERY_EMBEDDING_JSON for ChromaDB and Milvus"
  exit 0
fi

command -v curl >/dev/null 2>&1 || fail "RUNTIME=1 requires curl"
[[ -n "$CHROMA_COLLECTION_ID" ]] || fail "Set CHROMA_COLLECTION_ID for ChromaDB queries"
[[ -n "$QUERY_EMBEDDING_JSON" ]] || fail "Set QUERY_EMBEDDING_JSON to a JSON vector for ChromaDB and Milvus"

log "ChromaDB similarity search: ${RECON_QUERY}"
curl --silent --show-error --fail \
  --header 'Accept: application/json' \
  --header 'Content-Type: application/json' \
  --request POST \
  --data "{\"query_embeddings\":[${QUERY_EMBEDDING_JSON}],\"n_results\":5,\"where_document\":{\"\$contains\":\"NovaTech\"}}" \
  "${CHROMA_API_URL}/collections/${CHROMA_COLLECTION_ID}/query"
printf '\n'

log "Milvus filtered vector search: source == Architecture_Overview.md"
curl --silent --show-error --fail \
  --header 'Accept: application/json' \
  --header 'Content-Type: application/json' \
  --request POST \
  --data "{\"dbName\":\"default\",\"collectionName\":\"${MILVUS_COLLECTION}\",\"data\":[${QUERY_EMBEDDING_JSON}],\"annsField\":\"vector\",\"limit\":5,\"filter\":\"source == 'Architecture_Overview.md'\",\"outputFields\":[\"text\",\"source\",\"chunk_id\"]}" \
  "${MILVUS_URL}/v2/vectordb/entities/search"
printf '\n'

log "LightRAG entity graph query: ${ENTITY}"
curl --silent --show-error --fail \
  --header 'Accept: application/json' \
  "${LIGHTRAG_URL}/graph/entities?label=$(printf '%s' "$ENTITY" | sed 's/ /%20/g')"
printf '\n'

log "LightRAG relation graph query: ${ENTITY}"
curl --silent --show-error --fail \
  --header 'Accept: application/json' \
  "${LIGHTRAG_URL}/graph/relations?entity=$(printf '%s' "$ENTITY" | sed 's/ /%20/g')"
printf '\n'

log "Storage reconnaissance queries complete"
