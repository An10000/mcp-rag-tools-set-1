# mcp-rag-tools-set-1

MCP tools for RAG agents.

## Tools

| Tool | Description |
| --- | --- |
| `web_search` | Search the public web via [Tavily](https://tavily.com) and return titles, URLs and snippets. |
| `web_fetch` | Fetch a public web page and return its readable content as Markdown, in chunks for long pages. Needs no API key. |

## Setup

```bash
pip install -e ".[dev]"
```

Configuration is read from environment variables:

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `TAVILY_API_KEY` | yes | - | Tavily API key |
| `TAVILY_TIMEOUT` | no | `15` | Request timeout in seconds |
| `TAVILY_SEARCH_URL` | no | `https://api.tavily.com/search` | Override the API endpoint |

`TAVILY_API_KEY` is only needed by `web_search`.

`web_fetch` connects directly to the target site. It refuses private and local network
addresses (localhost, LAN, cloud metadata), ignores `HTTP(S)_PROXY` environment variables,
limits each fetch to 15 seconds and 5 MB, and reads HTML, plain text, JSON and XML only.

## Run

```bash
rag-mcp
```

Example MCP client configuration:

```json
{
  "mcpServers": {
    "rag-mcp": {
      "command": "rag-mcp",
      "env": { "TAVILY_API_KEY": "tvly-..." }
    }
  }
}
```

## Tests

```bash
pytest
```

Tests live in `evaluation/` and mock the Tavily API, DNS and HTTP, so no network access or API key is needed.
See [evaluation/web_search](evaluation/web_search/README.md) and [evaluation/web_fetch](evaluation/web_fetch/README.md)
for per-tool details and the manual live checks.
