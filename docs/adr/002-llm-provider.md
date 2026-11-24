# ADR-002: LLM provider and model

Status: Accepted (24 November 2026)

## Context

The LLM does two jobs: turning the query into structured search parameters, and classifying batches of profile snippets for relevance. Both need JSON that matches a schema, and both are simple enough for a small model. The figures behind this decision are in [../llm-research.md](../llm-research.md).

## Decision

- Default: Anthropic `claude-haiku-5-5` with effort `low`, called through the official `anthropic` SDK with `output_config.format` JSON Schema.
- Fallback: OpenAI `gpt-6-luna` through Chat Completions with strict `json_schema`, used when the primary call fails.
- Gemini (`gemini-3.1-flash-lite`, `responseJsonSchema`) is implemented and can be selected as either.
- All three sit behind one `LLMClient` protocol. `FallbackLLM` tries them in order.
- With no LLM configured, or when every call fails, a rule-based query parser and keyword matcher take over and the response says `"method": "rules"`.

## Options considered

| Option | Input / output $ per 1M | Outcome |
|---|---|---|
| Claude Haiku 5.5 | 0.10 / 0.50 | Chosen as default |
| GPT-6 Luna | 0.10 / 0.50 | Chosen as fallback; same price, different vendor |
| GPT-5 nano | 0.05 / 0.40 | Rejected: shuts down 11 December 2026 |
| GPT-5.4 nano | 0.20 / 1.25 | Rejected: deprecated, shuts down 1 April 2027 |
| Gemini 3.1 Flash-Lite | 0.25 / 1.50 | Supported, not default: about 2.5 to 3 times the cost |
| Claude Sonnet 5.5 | 2.00 / 10.00 | Rejected for now: no evidence the task needs it |

Haiku 5.5 and GPT-6 Luna tie on list price, and neither has been benchmarked on this task yet. Haiku 5.5 is the default because its SDK gives typed errors and an explicit effort control. `scripts/benchmark_llm.py` measures latency for both once keys are available.

## Consequences

- Changing provider or order is two environment variables.
- The model is never trusted for URLs, names or contact details; those come only from search results and websites. Quotes it cites as evidence are checked against the source text, and unverified quotes are discarded.
- An LLM outage lowers quality (rule-based matching is stricter and misses synonyms) but does not take the API down.
- Prompt caching does not help yet because both system prompts are below the minimum cacheable size.
