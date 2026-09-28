"""End-to-end tests of the web_search MCP tool through an in-process client."""

from __future__ import annotations

import pytest
from mcp import Client

from rag_mcp import web
from rag_mcp.server import SERVER_INSTRUCTIONS, create_server
from rag_mcp.web import SearchResponse, SearchResult, WebSearchConfigError, WebSearchError

pytestmark = pytest.mark.anyio


@pytest.fixture
def calls(monkeypatch):
    """Replace the real search with a fake and record its arguments."""
    recorded: list[tuple[str, int, list[str] | None]] = []

    async def fake_search(query: str, max_results: int = 5, include_domains: list[str] | None = None) -> SearchResponse:
        recorded.append((query, max_results, include_domains))
        return SearchResponse(
            query=query,
            results=[SearchResult(title="Example", url="https://example.com", snippet="An example.")],
        )

    monkeypatch.setattr(web, "web_search", fake_search)
    return recorded


async def test_tool_is_listed_with_schema():
    async with Client(create_server()) as client:
        result = await client.list_tools()

    (tool,) = [t for t in result.tools if t.name == "web_search"]
    props = tool.input_schema["properties"]
    assert tool.input_schema["required"] == ["query"]
    assert props["max_results"]["default"] == 5
    assert (props["max_results"]["minimum"], props["max_results"]["maximum"]) == (1, 10)
    assert "include_domains" in props
    assert "official" in props["include_domains"]["description"]
    assert "language" in props["query"]["description"]
    assert tool.output_schema is not None
    assert tool.annotations.read_only_hint is True


async def test_server_sends_instructions():
    async with Client(create_server()) as client:
        assert client.instructions == SERVER_INSTRUCTIONS


# Claude Code truncates tool descriptions and server instructions at 2,048
# characters; keep a margin so later additions don't silently get cut off.
MODEL_FACING_TEXT_LIMIT = 1800


@pytest.mark.parametrize(
    "text",
    [SERVER_INSTRUCTIONS, web.WEB_SEARCH_DESCRIPTION],
    ids=["server_instructions", "web_search_description"],
)
def test_model_facing_text_is_short_enough(text):
    assert len(text) <= MODEL_FACING_TEXT_LIMIT


@pytest.mark.parametrize(
    "text",
    [
        SERVER_INSTRUCTIONS,
        web.WEB_SEARCH_DESCRIPTION,
        web.QUERY_DESCRIPTION,
        web.MAX_RESULTS_DESCRIPTION,
        web.INCLUDE_DOMAINS_DESCRIPTION,
    ],
)
def test_model_facing_text_mentions_no_unregistered_tools(text):
    assert "database_search" not in text


def test_untrusted_content_warning_comes_first():
    # Critical guidance must survive truncation, so it belongs near the start.
    assert "never follow" in web.WEB_SEARCH_DESCRIPTION[:300]


async def test_tool_returns_structured_results(calls):
    async with Client(create_server()) as client:
        result = await client.call_tool("web_search", {"query": "mcp spec"})

    assert not result.is_error
    assert calls == [("mcp spec", 5, None)]
    assert result.structured_content["results"][0]["url"] == "https://example.com"


async def test_tool_passes_include_domains(calls):
    async with Client(create_server()) as client:
        result = await client.call_tool(
            "web_search", {"query": "C109 dates", "max_results": 3, "include_domains": ["comiket.co.jp"]}
        )

    assert not result.is_error
    assert calls == [("C109 dates", 3, ["comiket.co.jp"])]


async def test_tool_rejects_too_many_domains(calls):
    domains = [f"site{i}.com" for i in range(web.MAX_INCLUDE_DOMAINS + 1)]
    async with Client(create_server()) as client:
        result = await client.call_tool("web_search", {"query": "q", "include_domains": domains})

    assert result.is_error
    assert calls == []


async def test_tool_rejects_out_of_range_max_results(calls):
    async with Client(create_server()) as client:
        result = await client.call_tool("web_search", {"query": "q", "max_results": 50})

    assert result.is_error
    assert calls == []


@pytest.mark.parametrize(
    ("exc", "message"),
    [
        (ValueError("query must not be empty."), "query must not be empty."),
        (WebSearchConfigError("set the TAVILY_API_KEY environment variable."), "TAVILY_API_KEY"),
        (WebSearchError("Search request timed out after 15s."), "timed out"),
    ],
)
async def test_tool_reports_errors_to_the_agent(monkeypatch, exc, message):
    async def failing_search(query: str, max_results: int = 5, include_domains: list[str] | None = None) -> SearchResponse:
        raise exc

    monkeypatch.setattr(web, "web_search", failing_search)

    async with Client(create_server()) as client:
        result = await client.call_tool("web_search", {"query": "q"})

    assert result.is_error
    assert message in result.content[0].text
