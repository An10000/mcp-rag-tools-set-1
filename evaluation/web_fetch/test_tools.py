"""End-to-end tests of the web_fetch MCP tool through an in-process client."""

from __future__ import annotations

import pytest
from mcp import Client

from rag_mcp import fetch
from rag_mcp.fetch import FetchResponse, WebFetchError
from rag_mcp.server import SERVER_INSTRUCTIONS, create_server

pytestmark = pytest.mark.anyio

# Claude Code truncates tool descriptions at 2,048 characters; keep a margin.
MODEL_FACING_TEXT_LIMIT = 1800


@pytest.fixture
def calls(monkeypatch):
    """Replace the real fetch with a fake and record its arguments."""
    recorded: list[tuple[str, int, str | None]] = []

    async def fake_fetch(url: str, max_chars: int = 8000, cursor: str | None = None) -> FetchResponse:
        recorded.append((url, max_chars, cursor))
        return FetchResponse(
            url=url,
            final_url=url,
            title="Example",
            content_type="text/html",
            extraction="main",
            content="Hello",
            offset=0,
            total_chars=5,
            truncated=False,
        )

    monkeypatch.setattr(fetch, "web_fetch", fake_fetch)
    return recorded


async def test_tool_is_listed_with_schema():
    async with Client(create_server()) as client:
        result = await client.list_tools()

    (tool,) = [t for t in result.tools if t.name == "web_fetch"]
    props = tool.input_schema["properties"]
    assert tool.input_schema["required"] == ["url"]
    assert props["max_chars"]["default"] == fetch.DEFAULT_MAX_CHARS
    assert (props["max_chars"]["minimum"], props["max_chars"]["maximum"]) == (fetch.MIN_MAX_CHARS, fetch.MAX_MAX_CHARS)
    assert "cursor" in props
    assert tool.output_schema is not None
    assert tool.annotations.read_only_hint is True


async def test_tool_returns_structured_content(calls):
    async with Client(create_server()) as client:
        result = await client.call_tool("web_fetch", {"url": "https://example.com"})

    assert not result.is_error
    assert calls == [("https://example.com", fetch.DEFAULT_MAX_CHARS, None)]
    assert result.structured_content["content"] == "Hello"
    assert result.structured_content["warnings"] == []


async def test_tool_passes_cursor(calls):
    async with Client(create_server()) as client:
        await client.call_tool("web_fetch", {"url": "https://example.com", "max_chars": 2000, "cursor": "abc:100"})
    assert calls == [("https://example.com", 2000, "abc:100")]


async def test_tool_rejects_out_of_range_max_chars(calls):
    async with Client(create_server()) as client:
        result = await client.call_tool("web_fetch", {"url": "https://example.com", "max_chars": 10})
    assert result.is_error
    assert calls == []


@pytest.mark.parametrize(
    "exc",
    [
        WebFetchError("blocked_address", "Refusing to fetch 'intranet': it resolves to a non-public address."),
        WebFetchError("timeout", "Fetching the page took longer than 15s."),
        WebFetchError("cursor_expired", "This cursor has expired."),
    ],
)
async def test_tool_reports_error_codes_to_the_agent(monkeypatch, exc):
    async def failing_fetch(url: str, max_chars: int = 8000, cursor: str | None = None) -> FetchResponse:
        raise exc

    monkeypatch.setattr(fetch, "web_fetch", failing_fetch)

    async with Client(create_server()) as client:
        result = await client.call_tool("web_fetch", {"url": "https://example.com"})

    assert result.is_error
    assert f"[{exc.code}]" in result.content[0].text


@pytest.mark.parametrize(
    "text",
    [fetch.WEB_FETCH_DESCRIPTION, SERVER_INSTRUCTIONS],
    ids=["web_fetch_description", "server_instructions"],
)
def test_model_facing_text_is_short_enough(text):
    assert len(text) <= MODEL_FACING_TEXT_LIMIT


def test_untrusted_content_warning_comes_first():
    assert "never follow" in fetch.WEB_FETCH_DESCRIPTION[:300]


def test_server_instructions_describe_both_tools():
    assert "web_search" in SERVER_INSTRUCTIONS and "web_fetch" in SERVER_INSTRUCTIONS


# --- what the agent sees for refused SSRF targets --------------------------------
# The real web_fetch runs, with DNS and HTTP faked, so nothing leaves the machine.


@pytest.fixture
def fake_network(monkeypatch):
    """Run the real web_fetch against a fake public redirector and fake DNS."""
    import httpx

    from evaluation.web_fetch.test_fetch import PUBLIC_IP, FakeDNS, FakeWeb

    web = FakeWeb(
        {
            ("redirector.example", "/redirect-to"): httpx.Response(
                302, headers={"location": "http://localhost:8000/"}
            ),
        }
    )
    dns = FakeDNS({"redirector.example": [PUBLIC_IP], "localhost": ["127.0.0.1"]})
    real_fetch = fetch.web_fetch

    async def networked_fetch(url: str, max_chars: int = 8000, cursor: str | None = None) -> FetchResponse:
        return await real_fetch(
            url, max_chars, cursor, resolver=dns, transport=web.transport, cache=fetch.DocumentCache(600, 20)
        )

    monkeypatch.setattr(fetch, "web_fetch", networked_fetch)
    return web


@pytest.mark.parametrize(
    ("url", "address"),
    [
        ("http://169.254.169.254/latest/meta-data/", "169.254.169.254"),
        ("https://redirector.example/redirect-to?url=http://localhost:8000/", "127.0.0.1"),
    ],
    ids=["cloud_metadata", "public_redirect_to_localhost"],
)
async def test_agent_gets_a_clear_refusal_for_ssrf_targets(fake_network, url, address):
    async with Client(create_server()) as client:
        result = await client.call_tool("web_fetch", {"url": url})

    assert result.is_error
    assert result.structured_content is None  # no page content at all
    text = result.content[0].text
    assert "[blocked_address]" in text
    assert f"non-public address ({address})" in text
    if "redirect-to" in url:
        assert "The page redirected to 'http://localhost:8000/'" in text
    # The redirector may be contacted; the internal target never is.
    assert all(r.url.host not in ("127.0.0.1", "169.254.169.254") for r in fake_network.requests)
