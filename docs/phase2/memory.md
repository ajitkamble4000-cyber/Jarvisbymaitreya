# Phase 2: semantic memory search (contract for the web and verification agents)

Scope: memories gain an embedding, so users can search by meaning as well as by keyword. Keyword search stays the default and the fallback. Vectors live in PostgreSQL with pgvector. Embeddings come from the user's Google Gemini provider. The rest of the API is in `docs/api.md`, which this document does not edit.

Tested only with a fake embedding provider and a mocked Gemini transport. No live Gemini embedding call was made, and ranking quality with real embeddings is unverified.

## Environment

| Variable | Purpose |
| --- | --- |
| `GEMINI_EMBEDDING_MODEL` | Gemini embedding model, for example `gemini-embedding-001`. Unset means semantic search is off. Search then reports `fallback_reason: "no_embedding_model"`. |
| `EMBEDDING_DIMENSIONS` | Must be `768`, the size of `memories.embedding`. Other values are refused at runtime (see limitations). |

The user also needs a saved Gemini API key in AI Models. No new key is needed.

## Database

Migration `5b7e2d91c0a4` (after `44121059e6e8`) runs `CREATE EXTENSION IF NOT EXISTS vector` and adds `memories.embedding vector(768) NULL`. Creating the extension needs a privileged role. See the superuser note in `docs/operations.md`.

## Write path

`POST /api/v1/memories` and `PATCH /api/v1/memories/{id}` (content changed) try to embed the memory text before the row is saved.

- Success: the vector is stored with the memory.
- Any failure (no model, provider not configured, provider error, wrong vector size): the memory is saved with `embedding = NULL` and a warning is logged. The request still succeeds. Logs contain the reason code and never the memory text.
- A content edit that cannot be re-embedded clears the old vector, so a stale vector is never ranked. A category-only edit keeps the vector.
- Memories saved through the `save_memory` tool are saved unembedded. Run `POST /memories/reindex` to embed them.

## Endpoints

All routes sit under `/api/v1`. Unsafe methods also need the `X-CSRF-Token` header.

### `GET /memories`

Query parameters: `query` (max 200 characters), `category`, `semantic` (`true` or `false`, default `false`), `limit` (1 to 200, default 50), `offset`.

Response: the existing fields (`items`, `total`, `enabled`) plus two new fields.

```json
{
  "items": [
    {"id": "…", "category": "preference", "content": "Prefers strong coffee in the morning.",
     "source": "manual", "created_at": "…", "updated_at": "…", "expires_at": null}
  ],
  "total": 2,
  "enabled": true,
  "search_mode": "semantic",
  "fallback_reason": null
}
```

- `search_mode` is `"semantic"` or `"keyword"`. It is always present. Without `semantic=true` it is `"keyword"`.
- `fallback_reason` is set only when a semantic request was answered by keyword search. It is never silent.

| `fallback_reason` | Meaning |
| --- | --- |
| `no_embedding_model` | `GEMINI_EMBEDDING_MODEL` is not set. |
| `invalid_embedding_dimensions` | `EMBEDDING_DIMENSIONS` is not 768. |
| `no_stored_embeddings` | None of the user's live memories has a vector. The provider is not called. |
| `provider_not_configured` | The user has no saved Gemini key. |
| `provider_unavailable` | The query could not be embedded (provider error, timeout, or an unexpected response). |
| `no_query` | `semantic=true` was sent without a `query`. The list is returned in keyword order. |

Ordering in semantic mode:

1. Keyword matches that have no vector come first, newest first. They are included only when they match the query text by keyword.
2. Memories with a vector follow, ordered by cosine distance to the query, closest first. There is no relevance cutoff, so every live memory with a vector is ranked. Use `limit` and `offset` to page.

The query is always a bound parameter. Its SQL metacharacters and LIKE wildcards are treated as text.

### `POST /memories/reindex`

Embeds up to 200 of the caller's live memories that have no vector, in provider batches of up to 100. No request body.

Success, HTTP 200:

```json
{"updated": 200, "remaining": 5}
```

`remaining` counts the caller's live memories that still have no vector. Call again to continue.

Failure, after any successful batches are saved. The error body carries the same counts:

| HTTP | `error.code` | `error.details` |
| --- | --- | --- |
| 409 | `embedding_unavailable` | `{"reason": "no_embedding_model" \| "provider_not_configured" \| "invalid_embedding_dimensions", "updated": n, "remaining": n}` |
| 503 | `embedding_unavailable` | `{"reason": "provider_unavailable", "updated": n, "remaining": n}` |

Only the caller's memories are read or written.

## Provider contract

- `AIProvider.embed(texts, *, model, dimensions, timeout_seconds) -> list[list[float]]`. The base class raises `ProviderError(UNSUPPORTED)`.
- `ProviderCapabilities.embeddings` is `true` for Gemini and `false` for Hugging Face. The provider API response (`ProviderCapabilitiesOut`) does not expose this field yet.
- Gemini calls `POST {base}/v1beta/models/{model}:batchEmbedContents` with the key in `x-goog-api-key`. Each request sets `outputDimensionality` to the configured dimensions. The response must hold one finite vector of the right size per text. Inputs over 100 texts are refused with `invalid_request`.

## Limitations

- No live Gemini embedding call was made. Ranking quality with real embeddings is unverified.
- No relevance cutoff. Semantic search ranks every embedded live memory.
- No vector index. Search is a sequential scan. An HNSW index can be added in a later migration once the memory count grows.
- `EMBEDDING_DIMENSIONS` accepts only `768`. Settings refuse any other value at startup. A value set programmatically (for example in a test) still makes search report `invalid_embedding_dimensions` and writes skip embedding.
- Memories saved by the `save_memory` tool (task engine path) are not embedded at write time. The task engine does not receive the provider registry. Reindex covers them.
- Embedding calls are not written to the usage records.
