"""Tests for rag_mcp.web. The Tavily API is mocked with httpx.MockTransport."""

from __future__ import annotations

import json

import httpx
import pytest

from rag_mcp.web import (
    SNIPPET_MAX_CHARS,
    TAVILY_SEARCH_URL,
    WebSearchConfigError,
    WebSearchError,
    WebSearchSettings,
    web_search,
)

pytestmark = pytest.mark.anyio

SETTINGS = WebSearchSettings(api_key="tvly-test-key", retry_backoff=0)


def tavily_result(url: str, title: str = "Title", content: str = "Snippet", **extra) -> dict:
    return {"url": url, "title": title, "content": content, "score": 0.9, **extra}


def mock_client(*responses: httpx.Response | Exception) -> tuple[httpx.AsyncClient, list[httpx.Request]]:
    """Client that returns the given responses in order and records requests."""
    queue = list(responses)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), requests


def ok(results: list[dict]) -> httpx.Response:
    return httpx.Response(200, json={"query": "q", "results": results, "response_time": 0.5})


# --- successful searches ---------------------------------------------------


async def test_returns_structured_results_and_sends_expected_request():
    client, requests = mock_client(
        ok([tavily_result("https://example.com/a", "A", "About A", published_date="2026-09-01")])
    )

    response = await web_search("  python release  ", 3, settings=SETTINGS, client=client)

    assert response.query == "python release"
    assert len(response.results) == 1
    result = response.results[0]
    assert (result.title, result.url, result.snippet) == ("A", "https://example.com/a", "About A")
    assert result.published_date == "2026-09-01"

    (request,) = requests
    assert str(request.url) == TAVILY_SEARCH_URL
    assert request.headers["Authorization"] == "Bearer tvly-test-key"
    body = json.loads(request.content)
    assert body["query"] == "python release"
    assert body["max_results"] == 3


async def test_empty_results_is_not_an_error():
    client, _ = mock_client(ok([]))
    response = await web_search("nothing", settings=SETTINGS, client=client)
    assert response.results == []


async def test_limits_result_count():
    client, _ = mock_client(ok([tavily_result(f"https://example.com/{i}") for i in range(8)]))
    response = await web_search("q", 2, settings=SETTINGS, client=client)
    assert [r.url for r in response.results] == ["https://example.com/0", "https://example.com/1"]


async def test_dedupes_skips_invalid_items_and_cleans_text():
    client, _ = mock_client(
        ok(
            [
                tavily_result("https://example.com/page", "<b>Bold</b> &amp; title", "Line one\n\n  <p>line two</p>"),
                tavily_result("https://example.com/page/#section"),  # duplicate
                {"title": "no url"},
                tavily_result("ftp://example.com/file"),
                "not a dict",
                tavily_result("https://example.com/untitled", title=""),
            ]
        )
    )

    response = await web_search("q", settings=SETTINGS, client=client)

    assert [r.url for r in response.results] == ["https://example.com/page", "https://example.com/untitled"]
    assert response.results[0].title == "Bold & title"
    assert response.results[0].snippet == "Line one line two"
    assert response.results[1].title == "https://example.com/untitled"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("## \u200b Features", "Features"),
        ("# Status of Python versions\u00b6 The main branch", "Status of Python versions The main branch"),
        ("> quoted line\n# Heading\ntext", "quoted line Heading text"),
        ("> ```python\nx = 1\n```", "x = 1"),
        ("use `search_depth` here", "use search_depth here"),
        (r"api\_key and extract\_depth", "api_key and extract_depth"),
        ("<!-- hidden --> <DIV class='x'>text</DIV>", "text"),
        ("has _Python_ and _稳定_ highlights", "has Python and 稳定 highlights"),
        # Real content that looks like markup is kept.
        ("C# is fine, #1 stays, 3 > 2 stays", "C# is fine, #1 stays, 3 > 2 stays"),
        ("POST to <https://api.tavily.com/search> now", "POST to <https://api.tavily.com/search> now"),
        ("if x<y and y>z then", "if x<y and y>z then"),
        ("List<String> and Map<K, V>", "List<String> and Map<K, V>"),
        ("api_key, __init__, snake_case_name, _private", "api_key, __init__, snake_case_name, _private"),
        # Navigation text and tables are deliberately left alone.
        ("Skip to content | a | b |", "Skip to content | a | b |"),
    ],
)
async def test_cleans_markdown_noise_from_snippets(raw, expected):
    client, _ = mock_client(ok([tavily_result("https://example.com", content=raw)]))
    response = await web_search("q", settings=SETTINGS, client=client)
    assert response.results[0].snippet == expected


async def test_truncates_long_snippets():
    client, _ = mock_client(ok([tavily_result("https://example.com", content="word " * 400)]))
    response = await web_search("q", settings=SETTINGS, client=client)
    snippet = response.results[0].snippet
    assert len(snippet) <= SNIPPET_MAX_CHARS + 3
    assert snippet.endswith("...")


# --- input validation --------------------------------------------------------


@pytest.mark.parametrize("query", ["", "   ", "\n\t"])
async def test_rejects_blank_query(query):
    with pytest.raises(ValueError, match="query"):
        await web_search(query, settings=SETTINGS)


@pytest.mark.parametrize("max_results", [0, 11, -1, True, 2.5, "5"])
async def test_rejects_out_of_range_max_results(max_results):
    with pytest.raises(ValueError, match="max_results"):
        await web_search("q", max_results, settings=SETTINGS)


# --- configuration -----------------------------------------------------------


async def test_missing_api_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with pytest.raises(WebSearchConfigError, match="TAVILY_API_KEY"):
        await web_search("q")


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", " tvly-env ")
    monkeypatch.setenv("TAVILY_TIMEOUT", "7.5")
    monkeypatch.delenv("TAVILY_SEARCH_URL", raising=False)
    settings = WebSearchSettings.from_env()
    assert settings.api_key == "tvly-env"
    assert settings.timeout == 7.5
    assert settings.base_url == TAVILY_SEARCH_URL


@pytest.mark.parametrize("value", ["abc", "0", "-3"])
def test_settings_rejects_bad_timeout(monkeypatch, value):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-env")
    monkeypatch.setenv("TAVILY_TIMEOUT", value)
    with pytest.raises(WebSearchConfigError, match="TAVILY_TIMEOUT"):
        WebSearchSettings.from_env()


def test_settings_repr_hides_api_key():
    assert "tvly-test-key" not in repr(SETTINGS)


# --- upstream failures -------------------------------------------------------


async def test_unauthorized_is_config_error_without_retry():
    client, requests = mock_client(httpx.Response(401, json={"detail": "bad key"}))
    with pytest.raises(WebSearchConfigError, match="401"):
        await web_search("q", settings=SETTINGS, client=client)
    assert len(requests) == 1


async def test_bad_request_is_not_retried():
    client, requests = mock_client(httpx.Response(400))
    with pytest.raises(WebSearchError, match="400"):
        await web_search("q", settings=SETTINGS, client=client)
    assert len(requests) == 1


async def test_retries_transient_errors_then_succeeds():
    client, requests = mock_client(
        httpx.Response(503),
        httpx.ReadTimeout("slow"),
        ok([tavily_result("https://example.com")]),
    )
    response = await web_search("q", settings=SETTINGS, client=client)
    assert len(response.results) == 1
    assert len(requests) == 3


async def test_gives_up_after_max_retries():
    client, requests = mock_client(*[httpx.Response(429)] * 3)
    with pytest.raises(WebSearchError, match="429"):
        await web_search("q", settings=SETTINGS, client=client)
    assert len(requests) == SETTINGS.max_retries + 1


async def test_timeout_error_message():
    client, _ = mock_client(*[httpx.ConnectTimeout("t")] * 3)
    with pytest.raises(WebSearchError, match="timed out"):
        await web_search("q", settings=SETTINGS, client=client)


async def test_connection_error_message():
    client, _ = mock_client(*[httpx.ConnectError("refused")] * 3)
    with pytest.raises(WebSearchError, match="Could not reach"):
        await web_search("q", settings=SETTINGS, client=client)


async def test_invalid_json():
    client, _ = mock_client(httpx.Response(200, content=b"<html>oops</html>"))
    with pytest.raises(WebSearchError, match="JSON"):
        await web_search("q", settings=SETTINGS, client=client)


@pytest.mark.parametrize("payload", [[], {"no_results": True}, {"results": "nope"}])
async def test_unexpected_response_shape(payload):
    client, _ = mock_client(httpx.Response(200, json=payload))
    with pytest.raises(WebSearchError, match="unexpected response format"):
        await web_search("q", settings=SETTINGS, client=client)
