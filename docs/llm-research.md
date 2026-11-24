# LLM platform and model research

The API calls an LLM for two jobs:

1. Query understanding. One call per new query turns text such as "Football lovers in Washington DC in USA" into interest, audience, target type, city, state, country, keywords, role terms and location aliases.
2. Relevance classification. Candidate profiles are sent in batches of up to 20. For each one the model returns an entity type, a relevant flag, a confidence, and short quotes from the profile text that show the interest and the location.

Both jobs are short, structured and repetitive. They need reliable JSON, decent instruction following and low cost per call. They do not need deep reasoning or long context, so the comparison below focuses on the small "flash / nano / haiku" tier and includes one mid-tier model for reference.

Prices were read from each provider's official pricing or model pages on 24 November 2026. They are list prices for the standard (pay-as-you-go) tier in USD per million tokens.

## A. Comparison

| Provider | Model | Input $/1M | Output $/1M | Context (input) | JSON support | Notes |
|---|---|---|---|---|---|---|
| Anthropic | `claude-haiku-5-5` | 0.10 | 0.50 | 1M | JSON Schema via `output_config.format` (constrained decoding) | Prices apply to prompts up to 100K tokens (0.50 / 2.50 above that). Effort levels low to max. |
| OpenAI | `gpt-6-luna` | 0.10 (cached 0.01) | 0.50 | 922K max input, 1.05M window | Strict JSON Schema (`response_format`) | Named replacement for gpt-5-nano and gpt-5.4-nano. |
| OpenAI | `gpt-5.4-nano` | 0.20 (cached 0.02) | 1.25 | 272K max input, 400K window | Strict JSON Schema | Deprecated; shuts down 1 April 2027. |
| OpenAI | `gpt-5-nano` | 0.05 (cached 0.005) | 0.40 | not checked | Strict JSON Schema | Shuts down 11 December 2026, so not usable for a new build. |
| Google | `gemini-3.1-flash-lite` | 0.25 | 1.50 | 1,048,576 | `responseJsonSchema` | Stable, no shutdown date listed. |
| Google | `gemini-3.5-flash-lite` | 0.30 | 2.50 | 1,048,576 | `responseJsonSchema` | Google's recommended Flash-Lite for new projects. |
| Google | `gemini-3.8-flash` | 0.75 until 31 Dec 2026, then 1.50 | 3.75, then 7.50 | not checked | `responseJsonSchema` | Introductory pricing ends 31 December 2026. |
| Anthropic | `claude-sonnet-5-5` | 2.00 | 10.00 | 1M | JSON Schema | Reference point for a larger model. |

### Speed

No latency figures in this document are measured. The development machine had no provider API keys, so `scripts/benchmark_llm.py` could not be run. It times the query-understanding call for every provider that has a key in `.env` and prints median and worst latency per model:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_llm.py --runs 3
```

Estimate (not measured): models in this tier usually answer a request of this size in about one to three seconds. Relevance batches produce more output than the intent call, so they take longer. The pipeline runs them in parallel (up to three at once), so they overlap with each other but not with search.

### Accuracy

There is no public benchmark for this exact task, and I did not run a labelled evaluation, so I make no ranking claim between the small models. The design does not rely on the model being right:

- The model never produces URLs, names, emails or phone numbers. Those come only from search results and the prospect's own website.
- Every quote the model cites as evidence must appear verbatim in the profile text. A quote that does not is discarded, and without evidence for both interest and location the prospect is dropped.
- If the LLM fails or is not configured, a rule-based parser and keyword matcher take over, and the response reports `"method": "rules"`.

Quality differences between models therefore show up as recall (how many good prospects survive) rather than as invented data. A useful next step is a small labelled set of profiles per test query, scored for precision and recall per model.

## B. Model selection

| Requirement | What matters | Choice |
|---|---|---|
| Query understanding | Correct city/state/country split, sensible synonyms | Any model above; the output is sanitised and validated by Pydantic |
| Search query generation | Not an LLM job here | Queries are built deterministically by each platform adapter from the parsed intent |
| Relevance classification | Following "quote exactly, judge only from the text" | Small model at low effort, batched |
| Structured JSON | Schema-constrained output to avoid retries | All three providers support JSON Schema; the code uses it for each |
| Speed | Two to four calls per request, partly parallel | Small tier |
| Cost | Price per million tokens | Claude Haiku 5.5 and GPT-6 Luna tie at 0.10 / 0.50 |

## C. Cost optimisation

What the code already does:

- Deterministic work stays out of the LLM. URL parsing, profile validation, contact extraction (email-validator, phonenumbers), deduplication and location matching are plain code.
- A cheap pre-filter runs first. Only candidates with at least one keyword, role or location match reach the relevance call.
- Batching. Up to 20 candidates share one system prompt and one call.
- Bounded input and output. Each candidate's text is cut to 700 characters, and `max_tokens` is sized per batch (300 + 120 per item).
- Low effort. Anthropic requests send `effort: "low"`, which keeps thinking tokens down on a simple classification task.
- Caching with single-flight. Parsed intents are cached for 24 hours, search results, fetched pages and whole responses for 15 minutes. Concurrent identical requests share one upstream call instead of each making their own.
- No retry ladders. A failed or invalid LLM answer falls back to rules instead of retrying on a bigger model.

Further options that are not implemented:

- Prompt caching does not apply yet. Both system prompts are under 500 tokens, below the minimum cacheable prefix on current Anthropic models (512 to 4,096 tokens depending on model).
- Anthropic's Message Batches API halves the price for asynchronous jobs. It would suit a bulk "discover overnight" mode but not this synchronous endpoint.
- Moving the in-process caches to Redis would share them across workers and deployments.
- Search is the larger cost (see below). Combining several `site:` filters into one query would cut Serper calls, at the price of fewer results per platform.

## D. Final recommendation

| Item | Value |
|---|---|
| Recommended provider | Anthropic |
| Recommended model | `claude-haiku-5-5`, effort `low` |
| Fallback | OpenAI `gpt-6-luna` (same list price, different vendor, so an outage at one provider does not stop the service) |
| Estimated input tokens per request | about 9,000 (intent call about 560; two relevance batches of 20 candidates about 8,400) |
| Estimated output tokens per request | about 2,600 (intent about 150; relevance about 55 per candidate, plus a small thinking allowance) |
| Estimated LLM cost per request | about $0.0022 with Haiku 5.5 or GPT-6 Luna; $0.0062 with Gemini 3.1 Flash-Lite; $0.044 with Sonnet 5.5 |
| Estimated search cost per request | 11 to 12 Serper queries plus up to 4 cross-platform lookups: about $0.011 to $0.016 at $1 per 1,000 queries. YouTube Data API calls fall within the free daily quota. |
| Expected response time | Estimated 8 to 20 seconds end to end, dominated by search and website fetches (not measured) |

Token estimates use about four characters per token over the real prompt and schema sizes in the code (intent system prompt 1,467 characters plus a 702-character schema; relevance system prompt 941 characters plus a 582-character schema; at most 700 characters per candidate). A typical request is assumed to send 40 candidates to the relevance step. Repeat queries within the cache window cost nothing.

Why Haiku 5.5. It and GPT-6 Luna have the lowest list price of any current model in the table that is not scheduled for shutdown, and both offer schema-constrained JSON and a context window far larger than this task needs. With price tied, I chose Haiku 5.5 as the default because the Anthropic integration uses the official SDK with typed errors and an explicit effort control, and kept GPT-6 Luna as the configured fallback. Run `scripts/benchmark_llm.py` with both keys set and swap the order if Luna is faster on your network. The switch is two environment variables (`LLM_PROVIDER`, `LLM_FALLBACK_PROVIDER`).

How the system balances accuracy, speed and cost: rules handle everything that can be checked mechanically. The small model handles the two judgement calls. Its claims are verified against the source text before they count, and caching removes repeat work. Upgrading to a larger model is worth considering only if a labelled evaluation shows that the small model drops too many good prospects.

## Sources

- Anthropic model list and prices: Claude API documentation (models overview and pricing), cached copy dated 3 November 2026 in the Claude API skill used during development.
- OpenAI pricing: https://developers.openai.com/api/docs/pricing
- OpenAI deprecations: https://developers.openai.com/api/docs/deprecations
- OpenAI model pages: https://developers.openai.com/api/docs/models/gpt-6-luna and https://developers.openai.com/api/docs/models/gpt-5.4-nano
- Google pricing: https://aistudio.google.com/docs/pricing
- Google model pages: https://aistudio.google.com/docs/models/gemini-3.1-flash-lite and https://aistudio.google.com/docs/models/gemini-3.5-flash-lite
- Serper pricing: third-party summaries (the public pricing page returned no content); confirm at https://serper.dev before budgeting.
- Brave Search API plans: https://brave.com/search/api/
