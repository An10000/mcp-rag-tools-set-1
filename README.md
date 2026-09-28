# mcp-rag-tools-set-1

MCP tools for RAG agents.

## Tools

| Tool | Description |
| --- | --- |
| `web_search` | Search the public web via [Tavily](https://tavily.com) and return titles, URLs and snippets. |

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

Tests live in `evaluation/` and mock the Tavily API, so no network access or API key is needed.
