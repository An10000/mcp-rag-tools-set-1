"""Tests for rag_mcp.fetch. DNS is faked with a resolver function and HTTP with httpx.MockTransport."""

from __future__ import annotations

import gzip
import ipaddress

import anyio
import httpx
import pytest

from rag_mcp import fetch as fetch_module
from rag_mcp.fetch import (
    MAX_MAX_CHARS,
    MIN_MAX_CHARS,
    DocumentCache,
    WebFetchError,
    WebFetchSettings,
    is_public_address,
    parse_url,
    web_fetch,
)

pytestmark = pytest.mark.anyio

PUBLIC_IP = "93.184.215.14"
PUBLIC_IP_2 = "93.184.215.15"
PUBLIC_IPV6 = "2606:2800:21f:cb07:6820:80da:af6b:8b2c"

ARTICLE_HTML = """<html><head><title>Release notes</title>
<meta property="article:published_time" content="2026-09-01"></head>
<body><nav><a href="/">Home</a> <a href="/about">About</a></nav>
<article><h1>Version 2.0 released</h1>
<p>Version 2.0 brings a new parser, faster startup and a <a href="https://example.com/docs">documentation</a>
site. This paragraph is long enough to be treated as the main content of the page by the extractor.</p>
<p>Upgrading is straightforward: install the new package and restart the service. Older configuration
files keep working without changes, so most users need no further steps at all.</p>
<table><tr><th>Version</th><th>Date</th></tr><tr><td>2.0</td><td>2026-09-01</td></tr></table>
</article><footer>Copyright Example</footer></body></html>"""

INDEX_HTML = (
    "<html><head><title>Index</title></head><body><ul>"
    + "".join(f'<li><a href="/p{i}">Page {i}</a></li>' for i in range(30))
    + "</ul></body></html>"
)


class FakeDNS:
    def __init__(self, records: dict[str, list[str]] | None = None) -> None:
        self.records = records or {"example.com": [PUBLIC_IP]}
        self.calls: list[str] = []

    async def __call__(self, host: str, port: int) -> list[str]:
        self.calls.append(host)
        if host not in self.records:
            raise OSError("not found")
        return self.records[host]


class FakeWeb:
    """Serves responses by (Host header, path) and records requests."""

    def __init__(self, routes: dict[tuple[str, str], httpx.Response | Exception]) -> None:
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.routes[(request.headers["host"], request.url.path)]
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def html(body: str = ARTICLE_HTML, status: int = 200, **headers: str) -> httpx.Response:
    return httpx.Response(status, headers={"content-type": "text/html; charset=utf-8", **headers}, text=body)


async def fetch(url: str, web: FakeWeb, dns: FakeDNS | None = None, **kwargs):
    kwargs.setdefault("cache", DocumentCache(ttl=600, max_size=20))
    return await web_fetch(url, resolver=dns or FakeDNS(), transport=web.transport, **kwargs)


async def expect_error(code: str, url: str, web: FakeWeb, dns: FakeDNS | None = None, **kwargs) -> WebFetchError:
    with pytest.raises(WebFetchError) as info:
        await fetch(url, web, dns, **kwargs)
    assert info.value.code == code, str(info.value)
    assert str(info.value).startswith(f"[{code}]")
    return info.value


# --- address checks --------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",  # cloud metadata
        "100.64.0.1",  # carrier-grade NAT
        "0.0.0.0",
        "224.0.0.1",
        "255.255.255.255",
        "::1",
        "::",
        "fe80::1",
        "fc00::1",
        "ff02::1",
        "::ffff:127.0.0.1",  # IPv4-mapped
        "::ffff:192.168.1.1",
        "64:ff9b::c0a8:101",  # NAT64 of 192.168.1.1
        "2002:7f00:1::",  # 6to4 of 127.0.0.1
    ],
)
def test_non_public_addresses_are_refused(address):
    assert not is_public_address(ipaddress.ip_address(address))


@pytest.mark.parametrize("address", ["8.8.8.8", PUBLIC_IP, PUBLIC_IPV6, "64:ff9b::808:808"])
def test_public_addresses_are_allowed(address):
    assert is_public_address(ipaddress.ip_address(address))


@pytest.mark.parametrize(
    "url",
    ["", "   ", "ftp://example.com/file", "file:///etc/passwd", "https://user:pw@example.com/", "https:///path"],
)
def test_invalid_urls_are_rejected(url):
    with pytest.raises(WebFetchError) as info:
        parse_url(url)
    assert info.value.code == "invalid_url"


def test_overlong_url_is_rejected():
    with pytest.raises(WebFetchError) as info:
        parse_url("https://example.com/" + "a" * 3000)
    assert info.value.code == "invalid_url"


async def test_host_resolving_to_private_address_is_blocked_before_any_request():
    web = FakeWeb({})
    await expect_error("blocked_address", "https://intranet.test/", web, FakeDNS({"intranet.test": ["10.0.0.5"]}))
    assert web.requests == []


async def test_host_with_mixed_public_and_private_records_is_blocked():
    web = FakeWeb({})
    dns = FakeDNS({"mixed.test": [PUBLIC_IP, "127.0.0.1"]})
    await expect_error("blocked_address", "https://mixed.test/", web, dns)
    assert web.requests == []


@pytest.mark.parametrize("url", ["http://127.0.0.1:8080/admin", "http://[::1]/", "http://169.254.169.254/latest"])
async def test_private_ip_literals_are_blocked_without_dns(url):
    web, dns = FakeWeb({}), FakeDNS()
    await expect_error("blocked_address", url, web, dns)
    assert dns.calls == [] and web.requests == []


@pytest.mark.parametrize(
    ("host", "code"),
    [
        # Not IP literals for ipaddress, but some resolvers (glibc) turn them into 127.0.0.1.
        ("0x7f000001", "blocked_address"),
        ("2130706433", "blocked_address"),
        ("127.1", "blocked_address"),
        ("0177.0.0.1", "invalid_url"),  # httpx rejects octal IPv4 outright
    ],
)
async def test_numeric_ip_spellings_are_refused(host, code):
    web = FakeWeb({})
    await expect_error(code, f"http://{host}:8000/", web, FakeDNS({host: ["127.0.0.1"]}))
    assert web.requests == []


async def test_unresolvable_host_is_a_connection_error():
    await expect_error("connection_error", "https://nowhere.test/", FakeWeb({}), FakeDNS({}))


# --- pinned connections ----------------------------------------------------


async def test_connects_to_checked_ip_with_original_host_and_sni():
    web = FakeWeb({("example.com", "/news"): html()})
    dns = FakeDNS()

    await fetch("https://example.com/news?x=1", web, dns)

    (request,) = web.requests
    assert request.url.host == PUBLIC_IP
    assert request.url.query == b"x=1"
    assert request.headers["host"] == "example.com"
    assert request.extensions["sni_hostname"] == "example.com"
    assert dns.calls == ["example.com"]


async def test_host_header_keeps_non_default_port():
    web = FakeWeb({("example.com:8443", "/"): html()})
    await fetch("https://example.com:8443/", web)
    assert web.requests[0].url.port == 8443


async def test_ipv6_address_is_bracketed_in_url():
    web = FakeWeb({("v6.test", "/"): html()})
    await fetch("http://v6.test/", web, FakeDNS({"v6.test": [PUBLIC_IPV6]}))
    assert web.requests[0].url.host == PUBLIC_IPV6
    assert "sni_hostname" not in web.requests[0].extensions  # plain http


async def test_falls_back_to_next_address_when_connect_fails():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if request.url.host == PUBLIC_IPV6:
            raise httpx.ConnectError("unreachable")
        return html()

    response = await web_fetch(
        "https://example.com/",
        resolver=FakeDNS({"example.com": [PUBLIC_IPV6, PUBLIC_IP]}),
        transport=httpx.MockTransport(handler),
        cache=DocumentCache(600, 20),
    )
    assert calls == [PUBLIC_IPV6, PUBLIC_IP]
    assert response.extraction == "main"


async def test_certificate_error_is_explained_and_not_retried():
    error = httpx.ConnectError(
        "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: Hostname mismatch, certificate is not "
        "valid for 'example.com'. (_ssl.c:1144)"
    )
    web = FakeWeb({("example.com", "/"): error})
    result = await expect_error(
        "connection_error", "https://example.com/", web, FakeDNS({"example.com": [PUBLIC_IP, PUBLIC_IP_2]})
    )
    assert "TLS certificate" in str(result) and "Hostname mismatch" in str(result)
    assert len(web.requests) == 1


async def test_all_addresses_failing_is_a_connection_error():
    web = FakeWeb({("example.com", "/"): httpx.ConnectError("refused")})
    await expect_error("connection_error", "https://example.com/", web)


# --- redirects ---------------------------------------------------------------


async def test_follows_relative_redirect_and_reports_final_url():
    web = FakeWeb(
        {
            ("example.com", "/old"): httpx.Response(301, headers={"location": "/new"}),
            ("example.com", "/new"): html(),
        }
    )
    dns = FakeDNS()

    response = await fetch("https://example.com/old", web, dns)

    assert response.url == "https://example.com/old"
    assert response.final_url == "https://example.com/new"
    assert dns.calls == ["example.com", "example.com"]  # re-checked on every hop


async def test_redirect_to_private_host_is_blocked():
    web = FakeWeb({("example.com", "/"): httpx.Response(302, headers={"location": "http://internal.test/secret"})})
    dns = FakeDNS({"example.com": [PUBLIC_IP], "internal.test": ["192.168.0.10"]})

    await expect_error("blocked_address", "https://example.com/", web, dns)
    assert len(web.requests) == 1


async def test_dns_rebinding_between_hops_is_blocked():
    # The same host answers with a public address first, then a private one.
    answers = [[PUBLIC_IP], ["127.0.0.1"]]

    async def rebinding_dns(host: str, port: int) -> list[str]:
        return answers.pop(0)

    web = FakeWeb({("rebind.test", "/"): httpx.Response(302, headers={"location": "/again"})})
    with pytest.raises(WebFetchError) as info:
        await web_fetch("http://rebind.test/", resolver=rebinding_dns, transport=web.transport, cache=DocumentCache(600, 20))
    assert info.value.code == "blocked_address"
    assert [r.url.host for r in web.requests] == [PUBLIC_IP]


async def test_redirect_to_private_ip_literal_is_blocked():
    web = FakeWeb({("example.com", "/"): httpx.Response(302, headers={"location": "http://[::ffff:127.0.0.1]/"})})
    await expect_error("blocked_address", "https://example.com/", web)


async def test_redirect_to_other_scheme_is_rejected():
    web = FakeWeb({("example.com", "/"): httpx.Response(302, headers={"location": "file:///etc/passwd"})})
    await expect_error("invalid_url", "https://example.com/", web)


async def test_too_many_redirects():
    web = FakeWeb({("example.com", "/loop"): httpx.Response(302, headers={"location": "/loop"})})
    await expect_error("too_many_redirects", "https://example.com/loop", web, settings=WebFetchSettings(max_redirects=3))
    assert len(web.requests) == 4


# --- time and size limits --------------------------------------------------


async def test_slow_server_hits_total_timeout():
    async def handler(request: httpx.Request) -> httpx.Response:
        await anyio.sleep(5)
        return html()

    with pytest.raises(WebFetchError) as info:
        await web_fetch(
            "https://example.com/",
            resolver=FakeDNS(),
            transport=httpx.MockTransport(handler),
            settings=WebFetchSettings(timeout=0.2),
            cache=DocumentCache(600, 20),
        )
    assert info.value.code == "timeout"


async def test_slowly_dripping_body_hits_total_timeout():
    # Each chunk arrives quickly, so only a limit on the whole fetch stops this.
    async def drip():
        for _ in range(100):
            await anyio.sleep(0.05)
            yield b"<p>still loading</p>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=drip())

    with pytest.raises(WebFetchError) as info:
        await web_fetch(
            "https://example.com/",
            resolver=FakeDNS(),
            transport=httpx.MockTransport(handler),
            settings=WebFetchSettings(timeout=0.3),
            cache=DocumentCache(600, 20),
        )
    assert info.value.code == "timeout"


async def test_declared_content_length_over_limit_is_rejected():
    response = httpx.Response(200, headers={"content-type": "text/html", "content-length": str(10**9)}, content=b"x")
    web = FakeWeb({("example.com", "/"): response})
    await expect_error("too_large", "https://example.com/", web)


async def test_undeclared_large_body_is_stopped_while_reading():
    async def big():
        for _ in range(20):
            yield b"a" * 1024

    response = httpx.Response(200, headers={"content-type": "text/plain"}, content=big())
    web = FakeWeb({("example.com", "/"): response})
    await expect_error("too_large", "https://example.com/", web, settings=WebFetchSettings(max_bytes=8 * 1024))


async def test_decompressed_size_is_limited():
    bomb = gzip.compress(b"a" * 200_000)
    assert len(bomb) < 8 * 1024
    response = httpx.Response(200, headers={"content-type": "text/plain", "content-encoding": "gzip"}, content=bomb)
    web = FakeWeb({("example.com", "/"): response})
    await expect_error("too_large", "https://example.com/", web, settings=WebFetchSettings(max_bytes=8 * 1024))


# --- status codes and content types ----------------------------------------


@pytest.mark.parametrize("status", [404, 403, 500, 401])
async def test_error_status_is_http_error(status):
    web = FakeWeb({("example.com", "/"): html("<html><body>Error</body></html>", status=status)})
    error = await expect_error("http_error", "https://example.com/", web)
    assert str(status) in str(error)


async def test_challenge_header_is_reported_as_blocked():
    web = FakeWeb({("example.com", "/"): html("<html></html>", status=403, **{"cf-mitigated": "challenge"})})
    await expect_error("blocked", "https://example.com/", web)


async def test_challenge_page_body_is_reported_as_blocked():
    body = "<html><head><title>Just a moment...</title></head><body>Checking your browser</body></html>"
    web = FakeWeb({("example.com", "/"): html(body, status=503)})
    await expect_error("blocked", "https://example.com/", web)


async def test_unsupported_content_type():
    response = httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.7")
    error = await expect_error("unsupported_content_type", "https://example.com/", FakeWeb({("example.com", "/"): response}))
    assert "application/pdf" in str(error)


async def test_plain_text_is_returned_raw_with_declared_charset():
    text = "第一行\r\n\r\n\r\n\r\n第二行"
    response = httpx.Response(200, headers={"content-type": "text/plain; charset=big5"}, content=text.encode("big5"))
    result = await fetch("https://example.com/", FakeWeb({("example.com", "/"): response}))
    assert result.extraction == "raw"
    assert result.content == "第一行\n\n第二行"
    assert result.content_type == "text/plain"


async def test_json_is_returned_raw():
    response = httpx.Response(200, json={"version": "2.0"})
    result = await fetch("https://example.com/", FakeWeb({("example.com", "/"): response}))
    assert (result.extraction, result.content) == ("raw", '{"version":"2.0"}')


async def test_missing_content_type_is_sniffed_as_html():
    response = httpx.Response(200, content=ARTICLE_HTML.encode())
    result = await fetch("https://example.com/", FakeWeb({("example.com", "/"): response}))
    assert result.extraction == "main"


async def test_missing_content_type_without_html_is_unsupported():
    response = httpx.Response(200, content=b"\x00\x01binary")
    await expect_error("unsupported_content_type", "https://example.com/", FakeWeb({("example.com", "/"): response}))


# --- extraction --------------------------------------------------------------


async def test_article_extracts_main_content_as_markdown():
    result = await fetch("https://example.com/", FakeWeb({("example.com", "/"): html()}))

    assert result.extraction == "main"
    assert result.published_date == "2026-09-01"
    assert result.title
    assert "[documentation](https://example.com/docs)" in result.content
    assert "| 2.0 |" in result.content
    assert "Copyright Example" not in result.content
    assert result.warnings == []
    assert not result.truncated and result.next_cursor is None


async def test_index_page_keeps_all_entries():
    result = await fetch("https://example.com/", FakeWeb({("example.com", "/"): html(INDEX_HTML)}))
    assert "Page 0" in result.content and "Page 29" in result.content


def fake_extract(main: str, recall: str):
    def extract(html, favor_recall=False, **options):
        return recall if favor_recall else main

    return extract


@pytest.mark.parametrize(
    ("main", "recall", "expected"),
    [
        ("m" * 300, "r" * 900, "main"),  # main covers enough of the page: kept even if recall finds more
        ("m" * 10, "r" * 300, "recall"),
        ("m" * 10, "r" * 20, "full_text"),  # both cover too little: all page text
        ("", "", "full_text"),
        ("[m](https://example.com/very/long/link/target/" + "x" * 300 + ")", "", "full_text"),  # link targets don't count
    ],
)
async def test_extraction_falls_back_when_main_content_is_too_short(monkeypatch, main, recall, expected):
    monkeypatch.setattr(fetch_module.trafilatura, "extract", fake_extract(main, recall))
    result = await fetch("https://example.com/", FakeWeb({("example.com", "/"): html(INDEX_HTML)}))
    assert result.extraction == expected
    if expected == "full_text":
        assert "[Page 0](https://example.com/p0)" in result.content
        assert "[Page 29](https://example.com/p29)" in result.content


def test_page_text_keeps_blocks_lists_and_absolute_links():
    page = """<html><head><title>T</title><style>.x{}</style></head><body>
    <script>alert(1)</script><h1>Title<a href="#title">¶</a></h1><p>First   paragraph with a <a href="/docs/a.html"><b>link</b></a>.</p>
    <ul><li><a href="#top">Top</a></li><li><a href="https://other.org/b">Other</a>
      <ul><li><p>Nested <a href="../c.html">item</a></p></li></ul></li></ul>
    <div>Last<br>line</div></body></html>"""
    text = fetch_module._page_text(page, "https://example.com/base/index.html")
    assert text.splitlines() == [
        "Title",
        "First paragraph with a [link](https://example.com/docs/a.html).",
        "- Top",  # in-page link: plain text
        "- [Other](https://other.org/b)",
        "  - Nested [item](https://example.com/c.html)",
        "Last",
        "line",
    ]


@pytest.mark.parametrize(
    ("markdown", "expected"),
    [
        ("Use snake\\_case and \\*args, \\<b\\> and \\[1\\]", "Use snake_case and *args, <b\\> and [1]"),
        ("Keep `a\\_b` and\n```\ngrep '\\<word\\>'\n```\nbut fix c\\_d", "Keep `a\\_b` and\n```\ngrep '\\<word\\>'\n```\nbut fix c_d"),
    ],
    ids=["escapes", "code_kept"],
)
def test_clean_markdown_removes_escapes_outside_code(markdown, expected):
    assert fetch_module._clean_markdown(markdown, {}) == expected


def article(body: str) -> str:
    return f"<html><head><title>Doc</title></head><body><article>{body}</article></body></html>"


LONG = "This sentence is long enough to make the paragraph count as main content of the page. " * 3


async def test_relative_and_in_page_links_resolve_against_the_page():
    body = article(
        f'<h2><a href="#intro">Introduction</a></h2><p>{LONG} See <a href="../library/x.html">the library</a>, '
        f'<a href="#rules">the rules</a> and <a href="https://other.org/#top">another site</a>.</p><p>{LONG}</p>'
    )
    web = FakeWeb({("docs.example.com", "/3/whatsnew/3.15.html"): html(body)})
    dns = FakeDNS({"docs.example.com": [PUBLIC_IP]})
    result = await fetch("https://docs.example.com/3/whatsnew/3.15.html", web, dns)

    assert "[the library](https://docs.example.com/3/library/x.html)" in result.content
    assert "## Introduction" in result.content  # heading self-link unwrapped
    assert "the rules" in result.content and "#rules" not in result.content
    assert "[another site](https://other.org/#top)" in result.content


async def test_list_of_in_page_links_is_kept():
    # Highlight lists like Python's What's New: every item links into the page.
    items = "".join(
        f'<li><p><a href="https://peps.example.org/pep-{n}/"><strong>PEP {n}</strong></a>: '
        f'<a href="#feature-{n}"><span>Feature number {n} of this release</span></a></p></li>'
        for n in range(5)
    )
    body = article(f"<p>{LONG}</p><p>Interpreter improvements:</p><ul>{items}</ul><p>{LONG}</p>")
    result = await fetch("https://example.com/", FakeWeb({("example.com", "/"): html(body)}))
    assert "Feature number 0 of this release" in result.content
    assert "Feature number 4 of this release" in result.content


async def test_code_inside_list_items_keeps_its_lines():
    code = '<div class="highlight"><pre><span class="c1"># Correct:</span>\nimport os\nimport sys\n</pre></div>'
    body = article(f"<p>{LONG}</p><ul><li>Imports should usually be on separate lines:{code}</li></ul><p>{LONG}</p>")
    result = await fetch("https://example.com/", FakeWeb({("example.com", "/"): html(body)}))
    assert "```\n# Correct:\nimport os\nimport sys\n```" in result.content
    assert "Imports should usually be on separate lines:" in result.content
    assert "wfcode" not in result.content


async def test_section_with_comments_id_is_not_dropped_as_reader_comments():
    # PEP 8 has <section id="comments"> about code comments; trafilatura prunes such ids.
    body = article(
        f"<p>{LONG}</p><section id=\"comments\"><h2><a href=\"#comments\">Comments</a></h2>"
        "<p>Comments that contradict the code are worse than no comments, so keep them up to date.</p>"
        f"<section id=\"block-comments\"><h3>Block Comments</h3><p>{LONG}</p></section></section>"
    )
    result = await fetch("https://example.com/", FakeWeb({("example.com", "/"): html(body)}))
    assert "Comments that contradict the code" in result.content
    assert "Block Comments" in result.content


async def test_reader_comment_area_is_still_dropped():
    body = article(
        f"<p>{LONG}</p><p>{LONG}</p><div id=\"comments\" class=\"comments-area\"><h2>3 thoughts on this post</h2>"
        "<p>Great post, thanks for sharing it with everyone here!</p><form><textarea></textarea></form></div>"
    )
    result = await fetch("https://example.com/", FakeWeb({("example.com", "/"): html(body)}))
    assert "Great post" not in result.content


async def test_date_inside_code_sample_is_not_the_page_date():
    body = article(f"<p>{LONG}</p><pre>&gt;&gt;&gt; parse('2010-02-03 14:15')</pre><p>{LONG}</p>")
    result = await fetch("https://example.com/", FakeWeb({("example.com", "/"): html(body)}))
    assert result.published_date != "2010-02-03"


async def test_version_number_in_title_is_not_a_date():
    # htmldate parses "Python 3.15.0" in the title as 2010-08-03.
    body = f"<html><head><title>What's New in Python — Python 3.15.0</title></head><body><article><p>{LONG}</p></article></body></html>"
    result = await fetch("https://example.com/", FakeWeb({("example.com", "/"): html(body)}))
    assert result.published_date is None


async def test_dates_in_link_urls_are_not_guessed():
    link = '<a href="https://benchmarks.example.org/run/2026-09-25">7-8%</a>'
    body = article(f"<p>{LONG} Faster by {link} on average. Copyright 2001-2026.</p>")
    result = await fetch("https://example.com/", FakeWeb({("example.com", "/"): html(body)}))
    assert result.published_date is None


async def test_declared_publication_date_is_kept():
    result = await fetch("https://example.com/", FakeWeb({("example.com", "/"): html()}))
    assert result.published_date == "2026-09-01"


async def test_screen_reader_only_text_is_dropped():
    body = ARTICLE_HTML.replace("<article>", '<article><span class="visually-hidden">Hidden duplicate title</span>')
    body = body.replace("</article>", "<div hidden>Hidden panel</div></article>")
    result = await fetch("https://example.com/", FakeWeb({("example.com", "/"): html(body)}))
    assert "Hidden duplicate title" not in result.content
    assert "Hidden panel" not in result.content
    assert "Version 2.0 released" in result.content


async def test_heading_permalinks_are_removed():
    body = ARTICLE_HTML.replace("Version 2.0 released</h1>", 'Version 2.0 released<a href="#v2">¶</a></h1>')
    result = await fetch("https://example.com/", FakeWeb({("example.com", "/"): html(body)}))
    assert "¶" not in result.content


async def test_page_without_text_is_extraction_failed():
    web = FakeWeb({("example.com", "/"): html("<html><body><script>var x = 1;</script></body></html>")})
    await expect_error("extraction_failed", "https://example.com/", web)


async def test_html_without_charset_header_is_decoded():
    body = '<html><head><meta charset="utf-8"><title>T</title></head><body><p>' + "中文內容測試。" * 40 + "</p></body></html>"
    response = httpx.Response(200, headers={"content-type": "text/html"}, content=body.encode("utf-8"))
    result = await fetch("https://example.com/", FakeWeb({("example.com", "/"): response}))
    assert "中文內容測試" in result.content


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            "<html><head><title>Sign in</title></head><body><form><p>Please sign in</p>"
            '<input type="password" name="pw"></form></body></html>',
            "login",
        ),
        ('<html><body><div id="root"></div><noscript>You need to enable JavaScript.</noscript></body></html>', "JavaScript"),
        (
            "<html><body><p>Verify you are human</p>"
            '<div class="g-recaptcha" data-sitekey="x"></div></body></html>',
            "challenge",
        ),
    ],
    ids=["login", "js_shell", "captcha"],
)
async def test_suspicious_200_pages_get_warnings(body, expected):
    result = await fetch("https://example.com/", FakeWeb({("example.com", "/"): html(body)}))
    assert any(expected in w for w in result.warnings), result.warnings


# --- chunks and cursors ----------------------------------------------------


def long_text(paragraphs: int = 30) -> str:
    return "\n\n".join(f"Paragraph {i}: " + " ".join(["word"] * 60) for i in range(paragraphs))


def text_response(text: str) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/plain"}, text=text)


async def test_long_page_is_read_in_chunks_from_one_snapshot():
    text = long_text()
    web = FakeWeb({("example.com", "/"): text_response(text)})
    cache = DocumentCache(600, 20)

    first = await fetch("https://example.com/", web, cache=cache, max_chars=1000)
    assert first.truncated and first.next_cursor
    assert first.total_chars == len(text)
    assert first.content.endswith(" ") is False

    # The page changes upstream; the cursor must keep reading the old snapshot.
    web.routes[("example.com", "/")] = text_response("changed")
    chunks = [first.content]
    response = first
    while response.next_cursor:
        response = await fetch("https://example.com/", web, cache=cache, max_chars=1000, cursor=response.next_cursor)
        chunks.append(response.content)

    assert len(web.requests) == 1
    assert not response.truncated
    assert "".join(c + "\n\n" for c in chunks).strip() == text
    # Cuts happen at paragraph boundaries.
    assert all(c.startswith("Paragraph") for c in chunks)


async def test_cursor_offset_is_reported():
    web = FakeWeb({("example.com", "/"): text_response(long_text())})
    cache = DocumentCache(600, 20)
    first = await fetch("https://example.com/", web, cache=cache, max_chars=1000)
    second = await fetch("https://example.com/", web, cache=cache, max_chars=1000, cursor=first.next_cursor)
    assert first.offset == 0
    assert second.offset == int(first.next_cursor.rsplit(":", 1)[1]) > 0


async def test_cursor_for_another_url_is_rejected():
    web = FakeWeb({("example.com", "/"): text_response(long_text())})
    cache = DocumentCache(600, 20)
    first = await fetch("https://example.com/", web, cache=cache, max_chars=1000)
    await expect_error("invalid_cursor", "https://example.com/other", web, cache=cache, cursor=first.next_cursor)


@pytest.mark.parametrize("cursor", ["garbage", "abc:", ":12", "abc:-1"])
async def test_malformed_cursor_is_rejected(cursor):
    await expect_error("invalid_cursor", "https://example.com/", FakeWeb({}), cursor=cursor)


async def test_unknown_or_expired_cursor():
    await expect_error("cursor_expired", "https://example.com/", FakeWeb({}), cursor="unknown:100")

    web = FakeWeb({("example.com", "/"): text_response(long_text())})
    cache = DocumentCache(ttl=0, max_size=20)
    first = await fetch("https://example.com/", web, cache=cache, max_chars=1000)
    await expect_error("cursor_expired", "https://example.com/", web, cache=cache, cursor=first.next_cursor)


async def test_cache_evicts_oldest_documents():
    web = FakeWeb({("example.com", "/"): text_response(long_text())})
    cache = DocumentCache(ttl=600, max_size=2)
    first = await fetch("https://example.com/", web, cache=cache, max_chars=1000)
    await fetch("https://example.com/", web, cache=cache)
    await fetch("https://example.com/", web, cache=cache)
    await expect_error("cursor_expired", "https://example.com/", web, cache=cache, cursor=first.next_cursor)


@pytest.mark.parametrize("max_chars", [MIN_MAX_CHARS - 1, MAX_MAX_CHARS + 1, True, "1000"])
async def test_invalid_max_chars(max_chars):
    with pytest.raises(ValueError):
        await fetch("https://example.com/", FakeWeb({}), max_chars=max_chars)
