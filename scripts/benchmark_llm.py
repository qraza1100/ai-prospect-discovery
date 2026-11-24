"""Measure query-understanding latency for every LLM provider that has a key in .env.

Usage:
    python scripts/benchmark_llm.py --runs 3

Prints a Markdown table with median and worst latency per provider/model. Each run sends
the five assessment queries once, so the script costs about runs x 5 small requests per
provider. Token counts are estimated from character length (about 4 characters per token).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import DEFAULT_MODELS, Settings
from app.errors import LLMError
from app.services.llm_service import build_llm_client
from app.services.query_service import INTENT_SCHEMA, INTENT_SYSTEM_PROMPT
from scripts.run_test_queries import QUERIES


async def bench(provider: str, model: str, settings: Settings, runs: int) -> dict[str, object] | None:
    async with httpx.AsyncClient(timeout=60) as client:
        llm = build_llm_client(provider, model, settings, client)
        if llm is None:
            return None
        timings: list[float] = []
        errors = 0
        out_chars = 0
        for _ in range(runs):
            for query in QUERIES:
                started = time.perf_counter()
                try:
                    result = await llm.generate_json(
                        system=INTENT_SYSTEM_PROMPT,
                        prompt=json.dumps({"request": query}),
                        schema=INTENT_SCHEMA,
                        max_tokens=1024,
                    )
                except LLMError:
                    errors += 1
                    continue
                timings.append((time.perf_counter() - started) * 1000)
                out_chars += len(json.dumps(result))
        close = getattr(llm, "aclose", None)
        if close:
            await close()
    in_tokens = (len(INTENT_SYSTEM_PROMPT) + len(json.dumps(INTENT_SCHEMA)) + 60) // 4
    return {
        "provider": provider,
        "model": model,
        "calls": len(timings),
        "errors": errors,
        "median_ms": round(statistics.median(timings)) if timings else None,
        "max_ms": round(max(timings)) if timings else None,
        "est_in_tokens": in_tokens,
        "est_out_tokens": out_chars // 4 // max(1, len(timings)),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    settings = Settings()
    rows = [
        row
        for provider, model in DEFAULT_MODELS.items()
        if (row := await bench(provider, model, settings, args.runs)) is not None
    ]
    if not rows:
        print("No provider keys found in .env (ANTHROPIC_API_KEY, GEMINI_API_KEY, OPENAI_API_KEY).")
        return
    print("| Provider | Model | Calls | Errors | Median ms | Max ms | Est. input tok | Est. output tok |")
    print("|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(
            f"| {r['provider']} | {r['model']} | {r['calls']} | {r['errors']} | {r['median_ms']} | "
            f"{r['max_ms']} | {r['est_in_tokens']} | {r['est_out_tokens']} |"
        )


if __name__ == "__main__":
    asyncio.run(main())
