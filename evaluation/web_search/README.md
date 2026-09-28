# web_search evaluation

Tests and quality tracking for the `web_search` tool ([src/rag_mcp/web.py](../../src/rag_mcp/web.py)).

## Changelog

| Date | Change |
|---|---|
| 2026-09-28 | Moved `web_search_usage_feedback.md` from the repo root into this folder. |
| 2026-09-28 | Added this README with test usage and the issue checklist. Deferred non-English garbled text (N1) to future language-adaptation work. |
| 2026-09-28 | Agent evaluation round 2 of batch 1 (6 calls / 30 results). All batch 1 fixes confirmed; new findings N1–N10. |
| 2026-09-28 | Batch 1 wrap-up: HTML tag stripping limited to real tags, `_emphasis_` cleanup, Chinese query and regex noise checks added to `live_check.py`. |
| 2026-09-28 | Batch 1: server instructions, shorter tool description, snippet noise cleanup, snippet limit raised to 1000 chars. |
| 2026-09-28 | Agent evaluation round 1 (4 calls / 20 results). See [web_search_usage_feedback.md](web_search_usage_feedback.md). |
| 2026-09-28 | Initial `web_search` implementation with Tavily and unit tests. |

## Files

| File | Purpose | Network |
|---|---|---|
| `test_web.py` | Unit tests for `rag_mcp.web`: validation, config, retries, error mapping, result cleanup. Tavily is mocked with `httpx.MockTransport`. | No |
| `test_tools.py` | The `web_search` MCP tool through an in-process MCP client: schema, structured output, error reporting, server instructions, length limits on text sent to the model. | No |
| `live_check.py` | Manual check against the real Tavily API. Reports snippet length, truncation and leftover noise. Not collected by pytest. | Yes |
| `web_search_usage_feedback.md` | Agent evaluation reports, one timestamped section per round (in Chinese). | - |

## Running the tests

### Unit tests (no API key needed)

From the project root:

```bash
pytest                                   # all tests
pytest evaluation/web_search             # web_search tests only
pytest evaluation/web_search/test_web.py -k clean   # a subset by name
```

### Live check (real API, uses credits)

Needs `TAVILY_API_KEY`. Each query costs 1 credit (basic search depth).

PowerShell:

```powershell
$env:TAVILY_API_KEY = "tvly-..."
.venv\Scripts\python -m evaluation.web_search.live_check
.venv\Scripts\python -m evaluation.web_search.live_check "custom query" "another query"
```

bash:

```bash
TAVILY_API_KEY=tvly-... python -m evaluation.web_search.live_check
```

With no arguments it runs the default queries, the ones that exposed problems in earlier evaluations. Each result is printed with:

- snippet length and `TRUNCATED` / `full`
- `LEFTOVER NOISE: [...]`: markup that `_clean_text` should have removed. **This should never appear**; if it does, it's a regression.
- `known noise (not handled)`: noise we decided not to clean (see "Won't fix" below)

The last line is a summary, e.g. `20 results, 15 truncated, 0 with leftover noise`.

### Agent evaluation (manual)

1. Register the server with Claude Code (see the root [README](../../README.md)).
2. **Restart Claude Code** after changing tool descriptions. A `/mcp` Reconnect may keep serving the old tool description.
3. Check that the loaded description matches `WEB_SEARCH_DESCRIPTION`, then ask questions that should and should not trigger a search.
4. Record the results in [web_search_usage_feedback.md](web_search_usage_feedback.md).

## Issue checklist

IDs: `P*` = evaluation round 1, `N*` = evaluation round 2 (see [web_search_usage_feedback.md](web_search_usage_feedback.md)).

### Fixed

- [x] Description referenced the unregistered `database_search` tool (P5)
- [x] Description exceeded Claude Code's 2,048-char limit and was truncated (P6). Now 1062 chars, warning about untrusted content moved to the top, length test added
- [x] Cross-tool guidance moved to server instructions (`SERVER_INSTRUCTIONS` in `server.py`)
- [x] Snippet cut off before key facts at 500 chars (P1). Limit raised to 1000
- [x] Markdown noise in snippets (P7): `#` headings, blockquotes, code fences, backticks, `¶`, zero-width chars, `\_` escapes
- [x] Search-highlight emphasis such as `_Python_`
- [x] HTML tag stripping deleted autolinks (`<https://...>`) and comparisons (`x<y and y>z`). Only real HTML tags are stripped now

### Open, in planned order

**Batch 2: parameters and guidance**

- [ ] Expose `include_domains` so agents can restrict results to official sites (P4, N4). Twice in round 2, agents had to type the domain into the query
- [ ] Expose `topic` (`general` / `news`). `published_date` is null for general search (P2, N8: 30/30 null)
- [ ] Query-language hint in `QUERY_DESCRIPTION`: use the language of the official or local source. Round 2: Comiket English query 0/5 official results, Japanese 5/5 (N5)

**Needs investigation**

- [ ] Backslash-wrapped headings such as `\Rate limits\` (N3). Check the raw Tavily content before adding a rule; a naive rule would break Windows paths

**Later**

- [ ] Stale or low-quality results ranked high (P3, N7). Evaluate `search_depth`; confirm the `fast` / `ultra-fast` credit cost first, since Tavily's docs contradict themselves
- [ ] Snippet space taken by irrelevant chunks (N6). Consider exposing Tavily's `score`
- [ ] No way to read a full page when a snippet is not enough. Planned as a separate `web_extract` tool

### Deferred

- [ ] Non-English garbled text (N1): Tavily sometimes returns ISO-2022-JP pages undecoded, with ESC control chars. This is recoverable with `text.encode("ascii").decode("iso2022_jp")`. Deferred to language-adaptation work

### Won't fix

- Navigation text ("Skip to content", 上一主题/下一主题) and flattened tables: rules would delete real content, and tables sometimes hold the answer
- SVG/attribute text in snippets (N2): rare, and a rule for it is risky
- `[](` half-links and missing URLs ("POST to with"): present in Tavily's raw content, not caused by our cleanup
- Live-data pages with no content (N9, e.g. train timetables): outside what web search can answer
- Agent not clarifying ambiguous queries (N10): caller behavior, not the tool
