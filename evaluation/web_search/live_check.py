"""Manual check against the real Tavily API (needs TAVILY_API_KEY, uses credits).

Not collected by pytest. Run from the project root:

    python -m evaluation.web_search.live_check
    python -m evaluation.web_search.live_check "custom query" "another query"

Prints each result with its snippet length, whether it was truncated, and any
leftover noise patterns, so snippet quality can be compared between changes.
"""

from __future__ import annotations

import asyncio
import re
import sys

from rag_mcp.web import SNIPPET_MAX_CHARS, web_search

# Queries that exposed problems in earlier trials (see web_search_usage_feedback.md).
DEFAULT_QUERIES = [
    "latest Python release",
    'Tavily search_depth credits cost "fast" "ultra-fast"',
    "Comic Market 109 C109 dates December 2026",
    "Python \u6700\u65b0\u7a33\u5b9a\u7248",  # Chinese: "Python latest stable release"
]

# Noise that _clean_text should have removed, and noise deliberately left alone.
CLEANED_PATTERNS = {
    "heading #": re.compile(r"(?:^|\s)#{1,6}\s"),
    "backtick": re.compile(r"`"),
    "pilcrow": re.compile("\u00b6"),
    "escaped markdown": re.compile(r"\\[_*`#\[\]]"),
    "zero-width": re.compile("[\u200b\u200c\u200d\u2060\ufeff]"),
    "_emphasis_": re.compile(r"(?<![\w\\])_[^\s_]+_(?![\w_])"),
    "html tag": re.compile(r"</?(?:b|p|div|span|a|br|strong|em)\b[^<>]*>", re.IGNORECASE),
}
KNOWN_REMAINING_PATTERNS = ["Skip to content", "| ---", "--- ---", "[...]", "[](", "Image "]


async def check(query: str) -> tuple[int, int, int]:
    response = await web_search(query)
    truncated = leftover = 0
    print(f"\n=== {query}  ({len(response.results)} results)")
    for i, result in enumerate(response.results, 1):
        snippet = result.snippet
        is_truncated = snippet.endswith("...") and len(snippet) >= SNIPPET_MAX_CHARS - 50
        found = [name for name, pattern in CLEANED_PATTERNS.items() if pattern.search(snippet)]
        known = [p for p in KNOWN_REMAINING_PATTERNS if p in snippet]
        truncated += is_truncated
        leftover += bool(found)

        print(f"\n[{i}] {result.title}\n    {result.url}")
        flags = [f"{len(snippet)} chars", "TRUNCATED" if is_truncated else "full"]
        if found:
            flags.append(f"LEFTOVER NOISE: {found}")
        if known:
            flags.append(f"known noise (not handled): {known}")
        print(f"    {' | '.join(flags)}")
        print(f"    {snippet}")
    return len(response.results), truncated, leftover


async def main(queries: list[str]) -> None:
    totals = [0, 0, 0]
    for query in queries:
        for i, value in enumerate(await check(query)):
            totals[i] += value
    results, truncated, leftover = totals
    print(f"\n--- Summary: {results} results, {truncated} truncated, {leftover} with leftover noise")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or DEFAULT_QUERIES))
