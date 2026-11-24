# ADR-003: Profile consolidation rules

Status: Accepted (24 November 2026)

## Context

One person or business often has profiles on several platforms, and the assessment asks for them to be returned as one prospect. Merging two different people is worse than leaving one person split in two: it attaches someone else's contact details to a prospect. Every merge should be explainable by a rule a reviewer can check.

## Decision

Candidates are clustered with union-find (`app/services/deduplication_service.py`).

Decisive links merge regardless of names:

- A prospect's own website links to a social profile, through JSON-LD `sameAs` or a page link. Pages that link to more than two accounts on one platform are treated as directories, and "website by" credit links are skipped.
- Two profiles publish the same email address or phone number.

A scored link merges only when both conditions hold:

- the same normalised handle (dots, underscores and hyphens removed, at least four characters) appears on two different platforms, and
- the display names have a similarity of at least 0.8, or each name is just the handle.

Instagram and Threads share one account namespace, so between those two the handle alone is enough.

Two rules always apply:

- A shared name alone never merges.
- A cluster can never hold two different accounts from the same platform. The union-find refuses that merge unless the link is decisive.

After relevance scoring, the top prospects missing a platform get one search for their handle on those platforms. A hit joins only if it passes the scored-link rule.

## Options considered

| Option | Outcome |
|---|---|
| Name plus location matching | Rejected: namesakes in the same city are common. |
| LLM-based entity resolution | Rejected: not auditable, adds cost per pair, and the model could merge on a guess. |
| Embedding similarity | Rejected: no labelled pairs to tune a threshold. |

## Consequences

- Some real duplicates stay split, for example a LinkedIn slug `jane-roe-4b2a` and an Instagram handle `janeroe` with no website linking them.
- Every merge traces to one rule.
- The pairwise pass is O(n²) over candidates. At the current scale (under 200 candidates per request) that costs a few milliseconds. Blocking by handle would be needed for much larger batches.

The first draft of this record was written by a local Ollama model (qwen3.5:4b) and then corrected and edited.
