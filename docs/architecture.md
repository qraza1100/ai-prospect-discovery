# Software design description

The headings loosely follow the design viewpoints of IEEE 1016: context, composition, interfaces, interaction, information and rationale.

## Context

```
                +------------------+
client  ---->   |  FastAPI app     |  ---->  Serper / Brave (web search)
(HTTP JSON)     |  port 4000       |  ---->  YouTube Data API v3
                |                  |  ---->  Anthropic / OpenAI / Gemini (LLM)
                |                  |  ---->  prospect-owned websites (SSRF-safe fetcher)
                +------------------+
```

The service is stateless apart from in-process caches. It stores nothing on disk and has no database.

## Composition

| Layer | Modules | Responsibility |
|---|---|---|
| HTTP | `app/main.py` | Routing, validation errors, auth, rate limit, timeouts, security headers, error format |
| Composition root | `app/container.py` | Builds clients and services from `Settings`, closes them on shutdown |
| Orchestration | `app/services/prospect_service.py` | Runs the pipeline and assembles the response |
| Domain services | `query_service`, `search_service`, `profile_extractor`, `deduplication_service`, `relevance_service`, `llm_service` | One stage each |
| Platform adapters | `app/platforms/*` | Query building and profile URL rules per platform |
| Utilities | `app/utils/*` | Safe HTTP, retries, cache, rate limiter, normalisers, validators |

Dependencies point downward only. Services receive their collaborators in the constructor, which is how the tests swap in fakes without patching.

## Interfaces

External: `POST /prospect-discovery`, `GET /health`, `GET /docs` (OpenAPI). Request and response models are in `app/schemas.py`.

Internal extension points:

- `PlatformAdapter`: `build_queries(intent)`, `parse_url(url)`, `extract_name(title, profile)` and an overridable `discover(ctx)`.
- `SearchBackend`: `search(query, country_code, num) -> list[SearchResult]`.
- `LLMClient`: `generate_json(system, prompt, schema, max_tokens) -> dict`.

## Interaction

1. Validate the body. Look up or compute the intent (cache, then LLM, then rules).
2. Run every adapter at once with `asyncio.gather`. Each has its own timeout and error handling, and the search service caps concurrent upstream calls at four.
3. Build candidates from hits, merge repeats and extract snippet contacts.
4. Enrich up to eight websites, the ones with the most keyword and location signals first: robots.txt, home page, then the contact page if no email was found.
5. Cluster candidates, compute keyword and location signals, send the shortlisted ones to the LLM in batches (up to three batches at once) and verify cited quotes.
6. Run handle lookups for the top four accepted prospects, then build and sort the response.

## Information

`SearchHit` is a raw result. `ProfileCandidate` is one canonical profile with its text, contacts and linked URLs. A `Draft` is a cluster of candidates under evaluation, and an `Assessment` holds the relevance result. Only `Prospect` and the response models are exposed through the API.

## Design rationale

- [ADR-001](adr/001-search-backend.md): web search backend
- [ADR-002](adr/002-llm-provider.md): LLM provider and model
- [ADR-003](adr/003-profile-consolidation.md): profile consolidation rules

## Revisit as usage grows

- Move caches and the rate limiter to Redis before running more than one worker.
- Add a labelled evaluation set and track precision and recall per query and per model.
- Block candidate pairs by handle before the O(n²) identity pass if candidate counts grow past a few hundred.
- Return results progressively (streaming or a job ID) if end-to-end latency becomes a problem.
