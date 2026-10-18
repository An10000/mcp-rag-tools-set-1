# web_fetch evaluation

Tests and quality tracking for the `web_fetch` tool ([src/rag_mcp/fetch.py](../../src/rag_mcp/fetch.py)).

## Changelog

| Date | Change |
|---|---|
| 2026-10-18 | Fixes from agent evaluation round 1 (P1-P5 in [web_fetch_usage_feedback.md](web_fetch_usage_feedback.md)): code inside list items kept as multi-line fenced blocks; sections with ids like `comments` no longer dropped as reader comments; in-page links unwrapped to text so link-only lists survive; links made absolute against the page before extraction (fixes `../` links); `published_date` without extensive search, code samples, or years absent from the page; `full_text` keeps list nesting. Hex/decimal IP spellings covered by tests. |
| 2026-10-18 | Agent evaluation round 1 (8 calls in scope). See [web_fetch_usage_feedback.md](web_fetch_usage_feedback.md). |
| 2026-10-18 | Small fixes before agent evaluation: in-page links pointed at the site root (`https://host#x`, a trafilatura bug) and now point at the page; headings that are only self-links become plain text; trafilatura's Markdown escapes (`snake\_case`, `\<`) removed outside code; screen-reader-only and `hidden` elements dropped (they duplicated the page title); `full_text` description corrected. |
| 2026-10-18 | Initial `web_fetch` implementation: httpx + trafilatura, SSRF protection with pinned addresses, total time and decompressed size limits, extraction fallback, cursor-based chunks from a cached snapshot, error codes and warnings. Live check 10/10. |

## How it works

1. **URL check**: only `http`/`https`, no embedded credentials, at most 2048 characters.
2. **Address check (SSRF)**: the host is resolved once, and the fetch is refused if *any* address is not public (loopback, private, link-local, CGNAT, multicast, reserved, and IPv4 embedded in IPv4-mapped, 6to4, Teredo and NAT64 addresses).
3. **Pinned connection**: the request goes to the checked IP. The `Host` header and TLS SNI and certificate check use the original hostname. A different DNS answer at connect time (DNS rebinding) cannot change the target. Each connection gets a new client, so a pooled TLS connection is never reused for another hostname. `trust_env=False`, so proxy environment variables are ignored.
4. **Redirects** are followed by hand (at most 5), with steps 1 to 3 repeated on every hop.
5. **Limits**: 15 s for the whole network part (`anyio.fail_after`, which also catches slowly dripping bodies), and 5 MB of *decompressed* body, which also stops compression bombs.
6. **Content**: HTML is extracted; plain text, Markdown, CSV, JSON and XML are returned as is (`extraction: "raw"`); anything else is `unsupported_content_type`.
7. **Preparing the HTML** (before trafilatura, which gets these wrong):
   - elements hidden from readers (`visually-hidden`, `sr-only`, `hidden`) are dropped;
   - links are made absolute against the page URL (trafilatura resolves `../x` and `#x` against the site root);
   - links within the page are unwrapped to plain text, and heading permalinks (`¶`) removed: link-only lists of in-page links were dropped as navigation;
   - sections whose id looks like reader comments (`id="comments"`) but whose heading matches the id (generated docs) get a neutral id, since trafilatura prunes them;
   - `<pre>` inside `<li>`/`<dd>` is swapped for a placeholder and put back as a fenced block afterwards, since trafilatura flattens it into one line.
8. **Extraction** (in a worker thread):
   - `main`: trafilatura main content, as Markdown with links and tables, without comments.
   - `recall`: trafilatura with `favor_recall`, if `main` covers less than 25% of the page's text.
   - `full_text`: all text of the page's `<main>` / `role="main"` region (or the whole body), one block per line, with links kept as absolute Markdown links. Used if both of the above cover too little, which is typical for index pages.
   Coverage is compared with link targets removed, so link-heavy Markdown and plain text compare fairly.
   trafilatura's Markdown escapes (`snake\_case`, `\<`) are removed outside code.
9. **`published_date`**: htmldate without extensive search (which guesses from link URLs and any text), with code samples removed, and dropped when its year appears nowhere on the page (htmldate parses titles like "Python 3.15.0" as dates). Missing is preferred over wrong.
10. **Chunks**: content is cut at paragraph, line or word boundaries. Pages longer than `max_chars` are cached in memory (10 min, 20 pages) and `next_cursor` reads the next chunk of the *same* snapshot.

### Error codes

The tool error message starts with `[code]`:

| Code | Meaning |
|---|---|
| `invalid_url` | Not an http(s) URL, has credentials, too long, or a redirect to another scheme |
| `blocked_address` | Host or redirect target resolves to a non-public address |
| `connection_error` | DNS failure, connection refused, or an invalid TLS certificate (with the reason) |
| `timeout` | The fetch took longer than 15 s in total |
| `too_many_redirects` | More than 5 redirects |
| `http_error` | Non-2xx status (403 is *not* assumed to be a bot block) |
| `blocked` | Non-2xx status with a bot-challenge signal (`cf-mitigated: challenge`, challenge page markers) |
| `too_large` | Body over 5 MB after decompression |
| `unsupported_content_type` | Not HTML or text, e.g. PDF or images |
| `extraction_failed` | No readable text found |
| `invalid_cursor` / `cursor_expired` | Malformed cursor, cursor for another URL, or the snapshot expired (fetch again) |

### Warnings

A 200 response can still be the wrong page. When little text is extracted, the response's `warnings` can say it looks like a bot challenge or CAPTCHA, a login page (password field), or a page that needs JavaScript (`<noscript>`, empty `#root` / `#app` / `#__next`). These are warnings rather than errors because the heuristics can be wrong.

## Files

| File | Purpose | Network |
|---|---|---|
| `test_fetch.py` | Unit tests for `rag_mcp.fetch`: address checks, pinned connections, redirects, DNS rebinding, time and size limits, content types, extraction fallback, warnings, chunks and cursors. DNS and HTTP are faked. | No |
| `test_tools.py` | The `web_fetch` MCP tool through an in-process MCP client: schema, structured output, error codes, length limits on text sent to the model. | No |
| `live_check.py` | Manual check against real sites, including TLS failures (badssl.com) and refused local addresses. Not collected by pytest. | Yes |

## Running the tests

```bash
pytest evaluation/web_fetch
```

Live check (no API key needed):

```bash
python -m evaluation.web_fetch.live_check
python -m evaluation.web_fetch.live_check https://example.com/ https://docs.python.org/3/
```

With no arguments it runs the default URLs and expects each to succeed or fail with a specific code; the last line is e.g. `10/10 as expected`. On Windows set `PYTHONIOENCODING=utf-8` if the console cannot print some characters.

## Extraction measurements (2026-10-18)

Text length with links removed, as share of the page's full text, which decides between `main` and the fallbacks:

| Page | main / full | Result |
|---|---|---|
| docs.python.org `whatsnew/index.html` | 0.03 | `full_text` (version list kept) |
| docs.python.org `library/index.html` | 0.12 | `full_text` |
| github.com | 0.33 | `main` |
| blog.python.org | 0.53 | `main` |
| en.wikipedia.org article | 0.66 | `main` |
| bbc.com/news | 0.84 | `main` |
| peps.python.org PEP 8 | 0.94 | `main` |
| docs.python.org `ipaddress` | 0.99 | `main` |

## Issue checklist

IDs from [web_fetch_usage_feedback.md](web_fetch_usage_feedback.md).

### Fixed

- [x] P1: code blocks inside list items flattened into one line with `\#` escapes (PEP 8 Imports: Correct and Wrong examples became identical)
- [x] P2-A: PEP 8 "Comments" section missing. Cause: trafilatura prunes `id="comments"` and `id^="comment-"` as reader comments when comments are off
- [x] P2-B: link-only highlight lists (What's New 3.15) dropped as navigation. In-page links are now plain text
- [x] P3: `../library/x.html` resolved against the site root (missing `/3/`)
- [x] P4: wrong `published_date` on docs.python.org. Causes: a date in a code sample (`2010-02-03`), a link URL (`/run/2026-09-25`), and the title "Python 3.15.0" parsed as `2010-08-03`
- [x] P5: `full_text` lost list nesting
- [x] Hex and decimal IP spellings (`0x7f000001`, `2130706433`) were not verified in round 1: they are checked after resolution like any host; octal is rejected by httpx

### Open, in planned order

- [ ] Agent evaluation: register the server, ask questions that should and should not trigger a fetch, and record the results here
- [ ] PDF support (common for papers and official documents)
- [ ] JavaScript-rendered pages and screenshots: a separate browser tool (Playwright) if agents need it
- [ ] Optional Tavily Extract fallback for sites that block direct fetches
- [ ] Configurable limits (timeout, size) through environment variables, and a controlled proxy option
