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

# Default queries restricted to official domains (include_domains).
DOMAIN_QUERIES = [
    ("Comic Market 109 dates", ["comiket.co.jp"]),
    ("search_depth credits", ["docs.tavily.com"]),
]


async def check(query: str, include_domains: list[str] | None = None) -> tuple[int, int, int, int, int]:
    response = await web_search(query, include_domains=include_domains)
    truncated = leftover = dated = off_domain = 0
    scope = f"  [domains: {', '.join(include_domains)}]" if include_domains else ""
    print(f"\n=== {query}{scope}  ({len(response.results)} results)")
    for i, result in enumerate(response.results, 1):
        snippet = result.snippet
        is_truncated = snippet.endswith("...") and len(snippet) >= SNIPPET_MAX_CHARS - 50
        found = [name for name, pattern in CLEANED_PATTERNS.items() if pattern.search(snippet)]
        known = [p for p in KNOWN_REMAINING_PATTERNS if p in snippet]
        outside = bool(include_domains) and not any(d in result.url for d in include_domains)
        truncated += is_truncated
        leftover += bool(found)
        dated += result.published_date is not None
        off_domain += outside

        print(f"\n[{i}] {result.title}\n    {result.url}")
        flags = [
            f"{len(snippet)} chars",
            "TRUNCATED" if is_truncated else "full",
            f"date: {result.published_date or 'none'}",
        ]
        if outside:
            flags.append("OUTSIDE include_domains")
        if found:
            flags.append(f"LEFTOVER NOISE: {found}")
        if known:
            flags.append(f"known noise (not handled): {known}")
        print(f"    {' | '.join(flags)}")
        print(f"    {snippet}")
    return len(response.results), truncated, leftover, dated, off_domain


async def main(searches: list[tuple[str, list[str] | None]]) -> None:
    totals = [0, 0, 0, 0, 0]
    for query, domains in searches:
        for i, value in enumerate(await check(query, domains)):
            totals[i] += value
    results, truncated, leftover, dated, off_domain = totals
    print(
        f"\n--- Summary: {results} results, {truncated} truncated, {leftover} with leftover noise, "
        f"{dated} with published_date, {off_domain} outside include_domains"
    )


if __name__ == "__main__":
    if sys.argv[1:]:
        searches = [(q, None) for q in sys.argv[1:]]
    else:
        searches = [(q, None) for q in DEFAULT_QUERIES] + [(q, list(d)) for q, d in DOMAIN_QUERIES]
    asyncio.run(main(searches))
