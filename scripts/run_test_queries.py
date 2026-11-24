"""Run the five assessment queries against a running API and save each response.

Usage:
    python scripts/run_test_queries.py                       # http://localhost:4000
    python scripts/run_test_queries.py --base-url https://your-host --api-key KEY

Responses go to samples/<query-slug>.json. They contain whatever public data the
search providers returned, so review them before committing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import httpx

QUERIES = [
    "Football lovers in Washington DC in USA",
    "Digital marketing agency owners in New York USA",
    "Startup founders in London UK",
    "Cricket lovers in Ahmedabad India",
    "Restaurant owners in Dubai UAE",
]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://localhost:4000")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--out", default="samples")
    parser.add_argument("--max-results", type=int, default=None)
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    headers = {"X-API-Key": args.api_key} if args.api_key else {}
    failures = 0

    with httpx.Client(base_url=args.base_url, timeout=120, headers=headers) as client:
        for query in QUERIES:
            payload: dict[str, object] = {"query": query}
            if args.max_results:
                payload["max_results"] = args.max_results
            started = time.perf_counter()
            response = client.post("/prospect-discovery", json=payload)
            elapsed = time.perf_counter() - started
            body = response.json()
            (out / f"{slug(query)}.json").write_text(
                json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8"
            )

            if response.status_code != 200:
                failures += 1
                print(f"[{response.status_code}] {query}: {body.get('error', {}).get('message')}")
                continue
            statuses = ", ".join(
                f"{p['platform']}={p['status']}({p['profiles_found']})" for p in body["platforms"]
            )
            print(f"[200] {query}: {body['total_prospects']} prospects in {elapsed:.1f}s")
            print(f"      {statuses}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
