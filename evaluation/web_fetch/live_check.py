"""Manual check of web_fetch against real sites (needs network, no API key).

Not collected by pytest. Run from the project root:

    python -m evaluation.web_fetch.live_check
    python -m evaluation.web_fetch.live_check https://example.com/ https://docs.python.org/3/

For each URL prints the outcome (or error code), extraction method, title, date,
size, warnings and the start of the content. The default list also checks that
TLS verification still works when connecting to a pinned IP (badssl.com must fail)
and that local addresses are refused.
"""

from __future__ import annotations

import asyncio
import sys

from rag_mcp.fetch import WebFetchError, web_fetch

# (url, expected error code or None for success)
DEFAULT_URLS: list[tuple[str, str | None]] = [
    ("https://example.com/", None),
    ("https://docs.python.org/3/whatsnew/index.html", None),  # index page
    ("https://www.python.org/downloads/", None),
    ("https://en.wikipedia.org/wiki/Model_Context_Protocol", None),
    ("http://github.com/", None),  # http -> https redirect
    ("https://httpbin.org/json", None),  # JSON returned raw
    ("https://wrong.host.badssl.com/", "connection_error"),  # certificate for another host
    ("https://expired.badssl.com/", "connection_error"),
    ("http://localhost:8000/", "blocked_address"),
    ("http://169.254.169.254/latest/meta-data/", "blocked_address"),
]


async def check(url: str, expected: str | None) -> bool:
    print(f"\n=== {url}")
    try:
        result = await web_fetch(url, max_chars=2000)
    except WebFetchError as exc:
        ok = exc.code == expected
        print(f"    {'OK' if ok else 'UNEXPECTED'} error: {exc}")
        return ok

    ok = expected is None
    print(f"    {'OK' if ok else f'UNEXPECTED success (expected {expected})'}")
    print(
        f"    final_url: {result.final_url}\n"
        f"    {result.content_type} | extraction: {result.extraction} | {result.total_chars} chars"
        f" | truncated: {result.truncated} | date: {result.published_date or 'none'}"
    )
    print(f"    title: {result.title}")
    if result.warnings:
        print(f"    warnings: {result.warnings}")
    print("    " + result.content[:400].replace("\n", "\n    "))
    return ok


async def main(urls: list[tuple[str, str | None]]) -> None:
    results = [await check(url, expected) for url, expected in urls]
    print(f"\n--- Summary: {sum(results)}/{len(results)} as expected")


if __name__ == "__main__":
    urls = [(u, None) for u in sys.argv[1:]] or DEFAULT_URLS
    asyncio.run(main(urls))
