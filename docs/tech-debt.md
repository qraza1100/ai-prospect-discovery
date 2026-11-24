# Technical debt register

Scored as Priority = (Impact + Risk) x (6 - Effort), each on a 1 to 5 scale. Higher means fix sooner.

| # | Item | Type | Impact | Risk | Effort | Priority | Why it matters |
|---|---|---|---|---|---|---|---|
| 1 | Cache and rate limiter live in process memory | Architecture | 3 | 4 | 2 | 28 | With more than one worker, each process has its own limit (so clients get N times the quota) and its own cache (so repeat queries pay again). |
| 2 | No labelled evaluation of discovery quality | Test | 4 | 5 | 3 | 27 | Relevance and consolidation are tested with fakes only. Real precision and recall are unknown, so a prompt or model change can lower quality without any test failing. |
| 3 | Platform URL rules are hand-written | Code | 3 | 3 | 2 | 24 | If a platform changes its URL scheme, profiles are silently dropped. The rules are covered by tests, but nothing in production reports a falling acceptance rate. |
| 4 | Logs are plain text with no metrics | Infrastructure | 3 | 3 | 2 | 24 | No per-platform success rates, latency or cost per request to watch after deployment. |
| 5 | Non-prospect domain list is hand-maintained | Code | 2 | 3 | 2 | 20 | New directories and aggregators will slip through until someone adds them. |
| 6 | Cluster text sent to the LLM is cut at 700 characters | Code | 2 | 2 | 2 | 16 | Evidence from the last members of a large cluster can be cut off. |
| 7 | Serper pricing taken from third-party summaries | Documentation | 1 | 2 | 1 | 15 | The official pricing page returned no content during research, so cost estimates may be off. |
| 8 | Pairwise identity pass is O(n²) | Code | 1 | 2 | 2 | 12 | Fine below a few hundred candidates; would need blocking by handle at higher volume. |

Resolved during this review: there was no CI pipeline and no automated dependency updates. `.github/workflows/ci.yml` now runs ruff, mypy, the tests on Python 3.11 to 3.13 and `pip-audit`, and `.github/dependabot.yml` opens weekly dependency PRs. `pip-audit` reported no known vulnerabilities in `requirements.txt` on 24 November 2026.

## Remediation plan

1. Before the first real deployment: item 1 (Redis-backed limiter and cache, or run one worker) and item 4 (JSON logs with request ID, per-platform status counters and token usage).
2. With the first live API keys: item 2. Label 30 to 50 results per assessment query, then add a script that reports precision, recall and cost per model. Use it to confirm the model choice in ADR-002. Confirm Serper pricing (item 7) at the same time.
3. As usage grows: alert on a drop in accepted profiles per platform (item 3), and review rejected and accepted domains monthly (item 5). Items 6 and 8 only once real data shows they cost results or time.
