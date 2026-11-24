# AI Prospect Discovery API

A FastAPI service that takes a plain-language request such as "Football lovers in Washington DC in USA", finds matching public profiles on Google, LinkedIn, X, YouTube, Instagram, Facebook and Threads, merges profiles that belong to the same person or business, and returns them as JSON with any email, phone or WhatsApp contact the prospect has published.

Every prospect in a response has public evidence for both the interest and the location. Names, links and contact details come only from search results, the YouTube Data API or the prospect's own website. The LLM interprets the query and judges relevance, but it never supplies data.

- Endpoint: `POST http://localhost:4000/prospect-discovery`
- Interactive docs: `http://localhost:4000/docs`
- LLM comparison and recommendation: [docs/llm-research.md](docs/llm-research.md)
- Design description and decisions: [docs/architecture.md](docs/architecture.md), [docs/adr/](docs/adr/)
- Test plan and requirement traceability: [docs/test-plan.md](docs/test-plan.md)
- Known technical debt: [docs/tech-debt.md](docs/tech-debt.md)

## Quick start

Requires Python 3.11 or newer.

```powershell
git clone <your-repo-url> ai-prospect-discovery
cd ai-prospect-discovery
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
copy .env.example .env
```

On macOS or Linux use `source .venv/bin/activate` and `cp .env.example .env`.

Fill in `.env`. The service starts with no keys at all, but then it has nothing to search and answers 503. For real results you need:

| Key | Needed for | Where to get it |
|---|---|---|
| `SERPER_API_KEY` | Google results for every platform except YouTube | serper.dev (2,500 free queries) |
| `BRAVE_API_KEY` | Alternative to Serper (`SEARCH_PROVIDER=brave`) | brave.com/search/api |
| `YOUTUBE_API_KEY` | Official YouTube channel search | Google Cloud console, YouTube Data API v3 |
| `ANTHROPIC_API_KEY` | Query understanding and relevance checks (default LLM) | console.anthropic.com |
| `OPENAI_API_KEY` or `GEMINI_API_KEY` | Fallback LLM, or the primary one if you change `LLM_PROVIDER` | provider consoles |

Run it:

```powershell
python -m app
```

The server listens on `127.0.0.1:4000` by default (`HOST` and `PORT` change that). `GET /` redirects to `/docs`.

## Example

```powershell
curl.exe -s -X POST http://localhost:4000/prospect-discovery -H "Content-Type: application/json" -d "{\"query\": \"Football lovers in Washington DC in USA\"}"
```

The request body takes `query` (3 to 300 characters, must contain letters) and an optional `max_results` (1 to 100). Unknown fields are rejected.

The response below comes from the test suite. The pipeline is real, but the search results, LLM and website are fakes, so every name, handle, domain and number is synthetic. The full file, including the `intent` and `platforms` blocks, is [docs/sample-response-fixture.json](docs/sample-response-fixture.json).

```json
{
  "success": true,
  "query": "Football lovers in Washington DC in USA",
  "total_prospects": 2,
  "prospects": [
    {
      "name": "Kickoff DC Supporters Club",
      "location": "Washington DC, USA",
      "entity_type": "individual",
      "social_link": [
        { "platform": "Google", "link": "https://www.kickoff-dc.org/" },
        { "platform": "Facebook", "link": "https://www.facebook.com/dckickoffclub" },
        { "platform": "Instagram", "link": "https://www.instagram.com/dckickofffans/" },
        { "platform": "Email", "link": "mailto:hello@kickoff-dc.org" },
        { "platform": "WhatsApp", "link": "https://wa.me/12024561111" },
        { "platform": "Phone", "link": "tel:+12024561111" }
      ],
      "relevance": {
        "score": 0.95,
        "interest_match": true,
        "location_match": true,
        "method": "llm",
        "evidence": ["interest: \"football\"", "location: \"Washington DC\""]
      }
    },
    {
      "name": "Jane Roe",
      "location": "Washington DC, USA",
      "entity_type": "individual",
      "social_link": [
        { "platform": "Instagram", "link": "https://www.instagram.com/janeroe.dc/" },
        { "platform": "X", "link": "https://x.com/janeroe_dc" },
        { "platform": "Threads", "link": "https://www.threads.com/@janeroe.dc" },
        { "platform": "Email", "link": "mailto:jane.roe.dc@mailbox-demo.org" }
      ],
      "relevance": { "score": 0.95, "interest_match": true, "location_match": true, "method": "llm",
                     "evidence": ["interest: \"football\"", "location: \"Washington DC\""] }
    }
  ],
  "intent": { "interest": "Football", "audience": "Football lovers", "location": { "city": "Washington DC", "state": "District of Columbia", "country": "USA", "country_code": "us" }, "target_type": "individual", "...": "..." },
  "platforms": [ { "platform": "LinkedIn", "status": "ok", "profiles_found": 1, "detail": null }, "..." ]
}
```

The fields follow the assessment format. `name` is always present, `social_link` is always a non-empty array of `{platform, link}`, and email and phone appear inside it (`Email` as `mailto:`, `Phone` as `tel:`, `WhatsApp` as a `wa.me` link). Three additions help a reviewer check the result:

- `relevance` says why the prospect was kept. `evidence` holds quotes that were found verbatim in the public text, and `method` is `rules` when no LLM was used.
- `intent` shows how the query was understood. `source` is `llm`, or `rules` for the fallback parser.
- `platforms` reports each source as `ok`, `failed` (with a reason such as `rate limited`) or `skipped`, so one broken platform never hides the others.

`location` is filled only when the profile text names the place. A prospect is never given the query's city because it was searched for there.

### Running the five assessment queries

With the server running and keys in `.env`:

```powershell
python scripts\run_test_queries.py
```

The script posts each test query, prints the prospect count and per-platform status, and saves every response to `samples/<query>.json`. Those files hold real public data from the search providers, so read them before you commit them. Pass `--base-url https://your-host` to run the queries against a deployment.

## How it works

```
query
  -> validation (Pydantic)
  -> query understanding (LLM with JSON Schema, rule-based fallback, cached 24 h)
  -> platform discovery in parallel, one adapter per platform
       Google, LinkedIn, X, Instagram, Facebook, Threads, WhatsApp, Email: site-restricted web search
       YouTube: YouTube Data API v3 (web search if no key)
  -> candidate building: URL routed to its platform, non-profile URLs dropped, canonical link kept
  -> website enrichment: prospect-owned home pages fetched (robots.txt respected) for
       emails, phones, wa.me links and the social profiles the site links to
  -> consolidation (union-find on decisive and scored identity signals)
  -> relevance: keyword and location matching, then batched LLM check with verified quotes
  -> cross-platform lookup for top prospects missing a platform
  -> Pydantic-validated JSON response
```

Each platform is a class in `app/platforms/` with two jobs: build search queries for its site and decide whether a URL is a real profile. It also turns that URL into one canonical form, so `twitter.com/Jane/status/1` becomes `https://x.com/jane`. To add a platform, write an adapter and register it in `app/platforms/registry.py`.

### Which results count as prospects

Search results are full of posts, articles and directories, so each adapter accepts only profile URLs. Posts are reduced to their author's profile when the URL contains the author's handle. Search pages, groups, events, login walls and share links are dropped. On the open web the prospect link is the site's home page, never the search-result page. A domain list in `app/platforms/google.py` rejects search engines, news sites, review sites, directories, marketplaces and government or university domains, and a title check drops "Top 10 ..." style listicles.

### Consolidation

Merging two different people is worse than leaving one person split across two records, so the rules are conservative:

- A site that links to a social profile owns that profile. JSON-LD `sameAs` links take priority over page links. A page that links to more than two accounts on one platform is treated as a directory and ignored, and "website by" credit links are skipped.
- Two profiles that list the same email address or phone number merge.
- The same handle on two platforms merges only when the display names also match (similarity at least 0.8). Instagram and Threads share one account namespace, so the handle alone is enough there.
- A matching name alone never merges anything, and a merged record can never hold two different accounts on the same platform.

After relevance scoring, the top prospects that are missing a platform get one extra search for their handle on those platforms. A hit is attached only if it passes the same rules.

### Relevance

Each consolidated prospect is checked for interest evidence (keywords and role terms from the parsed intent) and location evidence (city aliases such as "DC", "DMV" or "District of Columbia"). Short acronyms only match in upper case, so "dc comics" is not a location. Candidates with no signal at all never reach the LLM. The rest go to the model in batches. For each candidate it returns an entity type, a relevant flag, a confidence and two quotes. A quote that cannot be found in the candidate's text is thrown away. A prospect is returned only if it is a person, business or organization (not an article or directory), the model marks it relevant with confidence of at least 0.5, and both interest and location are backed by a keyword match or a verified quote. Without an LLM, both keyword checks must pass.

### Contact details

Emails, phone numbers and WhatsApp numbers are taken only from text a prospect published: search snippets of their profile, `mailto:`, `tel:` and `wa.me` links, JSON-LD and visible text on their own site. Emails pass `email-validator` and a placeholder filter (`example.com`, `name@company.com`, image file names). Phone numbers must be valid for their region according to `phonenumbers`, and a number in free text also needs a word such as "call" or "phone" nearby. Pages that list more than five emails or three phone numbers are treated as directories and their contacts are dropped. Nothing is ever built from a name or a domain.

## Platform coverage and limits

| Platform | Source | Limitation |
|---|---|---|
| Google | Serper (Google results) or Brave | Depends on what the engine has indexed. Google's own Custom Search JSON API is closed to new customers and shuts down on 1 January 2027, so it is not used. |
| LinkedIn | `site:linkedin.com/in` and `/company` searches | LinkedIn has no public people-search API. Pages are never fetched, so only the title and snippet are used. |
| X | `site:x.com` / `site:twitter.com` searches | The official API has no free user search. Tweet URLs are reduced to the author profile. |
| YouTube | YouTube Data API v3 `search.list` (100 quota units) and `channels.list` (1 unit) | The free quota is 10,000 units a day, about 95 discovery requests. Without a key it falls back to web search. |
| Instagram | `site:instagram.com` searches | Graph API business discovery needs a business account and a known username, so it cannot search. Post URLs without a username are dropped. |
| Facebook | `site:facebook.com` searches | Page search in the Graph API is restricted. Groups, events, posts and share links are excluded. |
| Threads | `site:threads.net` / `site:threads.com` searches | The keyword search API needs app review. Links are normalised to threads.com. |
| WhatsApp | `wa.me` and `api.whatsapp.com/send` links a prospect published | There is no public directory. Group invite links are ignored. |
| Email, Phone | Snippets and prospect-owned websites | Only published, validated values. Many profiles publish none. |

Social sites are never scraped. The only pages the service downloads are prospect-owned websites, and only when their robots.txt allows it.

## Security

- SSRF protection for website fetches (`app/utils/http.py`). Only `http` and `https` on ports 80 and 443 are allowed, and URLs carrying credentials are refused. The host is resolved before connecting, and the request is refused unless every address is public. That rules out loopback, private ranges, link-local and cloud metadata addresses, CGNAT, multicast and IPv4-mapped IPv6. The connection is pinned to the checked IP, with the real host sent as the Host header and TLS SNI, so a second DNS answer cannot redirect it. Redirects are followed by hand (at most three) and each hop is checked again. Bodies are capped at 1 MB, only HTML is parsed, cookies are never stored and proxy environment variables are ignored.
- robots.txt is honoured following RFC 9309: a 4xx answer means allowed, while a 5xx answer or an unreachable host means disallowed.
- Inbound protection: a per-client rate limit (10 requests a minute by default, with `Retry-After` on 429), an optional `X-API-Key` checked in constant time (`API_AUTH_TOKEN`), and a whole-request timeout (504). Input is validated with control characters stripped, length bounds and no extra fields.
- Prompt injection: the query and all web text are sent to the LLM as JSON data with instructions to ignore commands inside it. Output is schema-constrained and validated, search operators (`site:`, `inurl:`, quotes) are stripped from model-generated terms, candidate IDs must match known IDs, and quotes are verified against the source.
- Secrets: keys live in environment variables as `SecretStr` and are never logged. The YouTube key travels in a header rather than the URL, and HTTP client debug logging is silenced. `.env` is git-ignored.
- Errors: every error response has the same `{"success": false, "error": {"code", "message"}}` shape. Unexpected exceptions are logged server-side and return a generic 500 with no stack trace or upstream response body.
- Headers: `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, a deny-all CSP on API routes, HSTS over HTTPS, and an `X-Request-ID` on every response. CORS is off unless `CORS_ALLOW_ORIGINS` lists origins.
- Privacy: the LLM is told to reject queries that target people by health, religion, ethnicity, sexual orientation or political opinion. Contact details are never written to logs.

## Configuration

All settings are environment variables (see `.env.example`).

| Variable | Default | Purpose |
|---|---|---|
| `HOST`, `PORT` | `127.0.0.1`, `4000` | Bind address |
| `SEARCH_PROVIDER` | `serper` | `serper` or `brave`; falls back to whichever key is set |
| `LLM_PROVIDER`, `LLM_MODEL` | `anthropic`, `claude-haiku-5-5` | Primary LLM (`anthropic`, `gemini`, `openai`, `none`) |
| `LLM_FALLBACK_PROVIDER`, `LLM_FALLBACK_MODEL` | `none` | Second LLM tried when the first fails |
| `LLM_EFFORT` | `low` | Anthropic effort level |
| `SEARCH_RESULTS_PER_QUERY` | `10` | Results per search call (Serper bills 1 credit up to 10) |
| `MAX_PROSPECTS` | `25` | Upper bound on returned prospects |
| `ENABLE_WEBSITE_ENRICHMENT`, `MAX_WEBSITES_PER_REQUEST` | `true`, `8` | Prospect-website fetching |
| `ENABLE_CROSS_PLATFORM_LOOKUP`, `MAX_CROSS_PLATFORM_LOOKUPS` | `true`, `4` | Extra handle searches for top prospects |
| `CACHE_TTL_SECONDS`, `INTENT_CACHE_TTL_SECONDS` | `900`, `86400` | Cache lifetimes |
| `RATE_LIMIT_PER_MINUTE` | `10` | Per-client limit; `0` disables it |
| `API_AUTH_TOKEN` | empty | When set, requests need a matching `X-API-Key` header |
| `CORS_ALLOW_ORIGINS` | `[]` | JSON list of allowed browser origins |
| `ENRICHMENT_TIMEOUT_SECONDS` | `15` | Deadline for all website fetches in one request; sites done by then keep their data |
| `REQUEST_TIMEOUT_SECONDS` | `90` | Whole-request deadline |

## Errors

| Status | `error.code` | When |
|---|---|---|
| 400 | `invalid_query` | The query does not describe people or businesses to find, or targets sensitive traits |
| 401 | `unauthorized` | `API_AUTH_TOKEN` is set and the header is missing or wrong |
| 422 | `invalid_request` | Body fails validation |
| 429 | `rate_limited` | Too many requests from one client |
| 503 | `discovery_unavailable` | Every source failed or none is configured (response includes `platforms`) |
| 504 | `timeout` | The request ran past `REQUEST_TIMEOUT_SECONDS` |
| 500 | `internal_error` | Unexpected failure; details are only in the server log |

## Tests and checks

```powershell
python -m pytest --cov=app
ruff check .
mypy app
```

174 tests run in a few seconds without network access, using fake search and LLM backends and mocked HTTP transports. They cover each module and the full pipeline through the HTTP API, and they include SSRF cases, rate limiting, auth, error shapes and the rule that unverifiable LLM quotes are rejected. Coverage is about 90%. `mypy` runs in strict mode. GitHub Actions runs all of this, plus `pip-audit`, on Python 3.11 to 3.13. [docs/test-plan.md](docs/test-plan.md) maps each assessment requirement to the code and tests that cover it.

## Deployment

The repository includes a `Dockerfile` (non-root user, health check) and a `render.yaml` blueprint.

```powershell
docker build -t prospect-discovery .
docker run --env-file .env -e HOST=0.0.0.0 -p 4000:4000 prospect-discovery
```

On Render, create a Blueprint from the repository and add the secret keys in the dashboard. Behind a reverse proxy, set `FORWARDED_ALLOW_IPS` to the proxy's address so the rate limiter sees real client IPs; `render.yaml` sets it to `*` because Render only exposes containers through its proxy. Rate limits and caches are in memory and per process. With several workers or instances, move them to a shared store such as Redis.

## Project layout

```
app/
  main.py                 FastAPI app, middleware, error handlers, routes
  container.py            builds and wires every service from settings
  config.py               settings from environment variables
  schemas.py              request and response models, internal data types
  errors.py               error types and their HTTP codes
  platforms/              one adapter per platform plus the registry
  services/
    query_service.py      LLM intent extraction and rule-based fallback
    search_service.py     Serper and Brave backends, caching, concurrency cap
    profile_extractor.py  candidate building and website enrichment
    deduplication_service.py
    relevance_service.py
    llm_service.py        Anthropic, Gemini and OpenAI clients, fallback chain
    prospect_service.py   pipeline orchestration
  utils/                  SSRF-safe fetcher, retries, cache, rate limiter, validators
tests/                    unit and API tests
scripts/                  live test-query runner and LLM latency benchmark
docs/                     LLM research, architecture, ADRs, test plan
```

## Known limitations

- Results depend on what the search engine has indexed and on snippet length. Profiles that show nothing about the interest or location in their snippet are dropped, even if they would qualify.
- The relevance step has been tested with a fake LLM only. No labelled precision or recall figures exist yet, and latency has not been measured (see [docs/llm-research.md](docs/llm-research.md)).
- Consolidation favours precision over recall, so some real duplicates stay as separate prospects.
- `entity_type` comes from the LLM, or from URL type when no LLM runs, and can be wrong for small businesses run by one person.
- The in-memory cache and rate limiter do not span processes.
