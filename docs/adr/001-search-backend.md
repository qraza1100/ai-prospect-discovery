# ADR-001: Web search backend

Status: Accepted (24 November 2026)

## Context

The API has to find public profiles on Google, LinkedIn, X, Instagram, Facebook and Threads. LinkedIn, Instagram, Facebook and Threads offer no public people-search API to new developers, and X's official API has no free user search. Google's Custom Search JSON API is closed to new customers and shuts down on 1 January 2027. Scraping the platforms directly would break their terms and their login walls.

## Decision

Discovery runs site-restricted web searches (`site:linkedin.com/in "football" "Washington DC"`) through a `SearchBackend` interface with two implementations:

- Serper (default) returns Google results as JSON. New accounts get 2,500 free queries, after which prepaid packs cost about $1 per 1,000 queries. `site:` operators work.
- Brave Search API is the alternative, selected with `SEARCH_PROVIDER=brave`. It costs $5 per 1,000 requests with $5 of free credit a month and uses Brave's own index.

If the chosen provider has no key and the other one does, the service uses the other one and logs a warning. YouTube is the exception: it uses the official YouTube Data API v3 when a key is set.

## Options considered

| Option | Outcome |
|---|---|
| Google Custom Search JSON API | Rejected: closed to new customers, shutting down. |
| Scrapers per platform | Rejected: against platform terms, blocked by login walls, high upkeep. |
| Official platform APIs | Used where they allow search (YouTube). The others cannot search for people. |
| Serper only | Rejected: one vendor outage or price change would stop discovery. |

## Consequences

- Results depend on what the search engine has indexed, and snippets are short. A profile that is not indexed, or whose snippet says nothing about the interest or place, is missed.
- Switching provider is one environment variable.
- A typical request sends 11 or 12 queries, plus up to four cross-platform handle lookups. At Serper's pack price that is roughly $0.011 to $0.016 per request, more than the LLM cost.
- LinkedIn and other social pages are never fetched. Only titles and snippets from the search results are used.

The first draft of this record was written by a local Ollama model (qwen3.5:4b) and then corrected and edited.
