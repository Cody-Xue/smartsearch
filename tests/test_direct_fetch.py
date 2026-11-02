import asyncio
import gzip
import hashlib
import socket
import ssl
import time

import httpx
import pytest

from smart_search import direct_fetch, service
from smart_search.provider_errors import ProviderCallError


def mock_http(monkeypatch, handler, addresses=None):
    requests = []
    real_client = httpx.AsyncClient

    async def resolve(host, port, **kwargs):
        values = addresses(host) if callable(addresses) else (addresses or ["93.184.216.34"])
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port)) for address in values]

    def respond(request):
        requests.append(request)
        return handler(request)

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    monkeypatch.setattr(direct_fetch, "_configured_proxy", lambda url: None)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs))
    return requests


@pytest.mark.parametrize("url, expected", [
    ("https://raw.githubusercontent.com/owner/repo/main/Include/Python.h", True),
    ("https://gist.githubusercontent.com/owner/id/raw/README", True),
    ("https://gitlab.example.org/owner/repo/-/raw/main/LICENSE", True),
    ("https://example.org/src/other.py?ref=main", True),
    ("https://example.org/data.JSON", True),
    ("https://example.org/LICENSE", True),
    ("https://github.com/owner/repo/blob/main/code.py", False),
    ("https://gitlab.example.org/owner/repo/-/blob/main/code.py", False),
    ("https://bitbucket.org/owner/repo/src/main/code.py", False),
    ("https://codeberg.org/owner/repo/src/branch/main/code.py", False),
    ("https://docs.python.org/library/pathlib.html", False),
    ("https://example.org/search?q=file.py", False),
    ("https://example.org/news.php", False),
    ("https://example.org/search.pl", False),
    ("https://raw.githubusercontent.com/owner/repo/main/index.php", True),
])
def test_recognizes_raw_text_without_stealing_html_routes(url, expected):
    assert direct_fetch.is_raw_text_url(url) is expected


@pytest.mark.asyncio
async def test_source_preserves_angles_identifiers_crlf_bom_and_bytes_without_keys(monkeypatch):
    body = b'\xef\xbb\xbf#ifndef Py_PYTHON_H\r\n#define Py_PYTHON_H\r\n\r\n#include <assert.h>\r\n\t// \'quoted\' and \\n\r\n#endif\r\n'
    monkeypatch.setenv("TAVILY_API_KEY", "must-not-be-sent")
    monkeypatch.setenv("HTTPS_PROXY", "https://user:password@127.0.0.1:9999")
    requests = mock_http(monkeypatch, lambda request: httpx.Response(200, content=body, headers={"Content-Type": "text/plain; charset=utf-8"}))

    result = await service.fetch("https://raw.githubusercontent.com/owner/repo/ref/code.h")

    assert result["ok"] is True
    assert result["provider"] == "direct"
    assert result["content"].encode(result["encoding"]) == body
    assert result["sha256"] == hashlib.sha256(body).hexdigest()
    assert result["byte_count"] == len(body)
    assert result["truncated"] is False
    assert [attempt["provider"] for attempt in result["provider_attempts"]] == ["direct"]
    assert len(requests) == 1
    assert requests[0].url.host == "93.184.216.34"
    assert requests[0].headers["Host"] == "raw.githubusercontent.com"
    assert requests[0].extensions["sni_hostname"] == "raw.githubusercontent.com"
    assert "authorization" not in requests[0].headers
    assert "cookie" not in requests[0].headers
    assert "must-not-be-sent" not in str(requests[0].headers)


@pytest.mark.asyncio
async def test_hash_describes_decompressed_body_and_charset_is_reversible(monkeypatch):
    body = "# café\r\n".encode("iso-8859-1")
    mock_http(monkeypatch, lambda request: httpx.Response(200, content=gzip.compress(body), headers={
        "Content-Type": "text/plain; charset=iso-8859-1", "Content-Encoding": "gzip",
    }))
    result = await direct_fetch.fetch_raw_text("https://example.org/code.py")
    assert result["content"].encode(result["encoding"]) == body
    assert result["sha256"] == hashlib.sha256(body).hexdigest()
    assert result["hash_scope"] == "decoded_response_body"


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "http://127.0.0.1/code.py", "http://[::1]/code.py", "http://169.254.169.254/code.py",
    "http://10.0.0.1/code.py", "http://[::ffff:127.0.0.1]/code.py", "http://224.0.0.1/code.py",
    "file:///C:/private/code.py", "https://user:secret@example.org/code.py",
])
async def test_rejects_non_public_urls_before_sending(monkeypatch, url):
    requests = mock_http(monkeypatch, lambda request: pytest.fail("must not connect"))
    with pytest.raises(ProviderCallError) as caught:
        await direct_fetch.fetch_raw_text(url)
    assert caught.value.error_type == "parameter_error"
    assert requests == []


@pytest.mark.asyncio
async def test_rejects_mixed_public_private_dns_and_dns_rebinding(monkeypatch):
    requests = mock_http(monkeypatch, lambda request: pytest.fail("must not connect"), ["93.184.216.34", "127.0.0.1"])
    with pytest.raises(ProviderCallError, match="non-public"):
        await direct_fetch.fetch_raw_text("https://attacker.example/code.py")
    assert requests == []


@pytest.mark.asyncio
async def test_redirect_resolves_each_hop_without_cookies_or_ambient_auth(monkeypatch):
    def handler(request):
        if request.headers["Host"] == "first.example":
            return httpx.Response(302, headers={"Location": "https://second.example/real.py", "Set-Cookie": "secret=never-forward"})
        assert request.url.host == "93.184.216.35"
        assert request.headers["Host"] == "second.example"
        assert request.extensions["sni_hostname"] == "second.example"
        assert "cookie" not in request.headers
        assert "authorization" not in request.headers
        return httpx.Response(200, content=b"print('ok')\n")

    requests = mock_http(monkeypatch, handler, lambda host: ["93.184.216.34" if host == "first.example" else "93.184.216.35"])
    result = await direct_fetch.fetch_raw_text("https://first.example/code.py")
    assert len(requests) == 2
    assert result["final_url"] == "https://second.example/real.py"


@pytest.mark.asyncio
@pytest.mark.parametrize("location", ["https://127.0.0.1/private.py", "http://example.org/code.py", "https://user:secret@example.org/code.py"])
async def test_redirect_cannot_enter_private_network_downgrade_or_add_credentials(monkeypatch, location):
    requests = mock_http(monkeypatch, lambda request: httpx.Response(302, headers={"Location": location}))
    with pytest.raises(ProviderCallError) as caught:
        await direct_fetch.fetch_raw_text("https://public.example/code.py")
    assert caught.value.error_type == "parameter_error"
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("body, content_type, error_type", [
    (b"<html>Login required</html>", "text/html", "quality_error"),
    (b" <!DOCTYPE html><title>Access denied</title>", "text/plain", "quality_error"),
    (b"PDF", "application/pdf", "quality_error"),
    (b"binary\x00data", "application/octet-stream", "quality_error"),
    (b"\xff", "text/plain; charset=utf-8", "parse_error"),
    (b"text", "text/plain; charset=unknown-charset", "parse_error"),
])
async def test_malformed_source_is_explicit_failure_without_lossy_extractor(monkeypatch, body, content_type, error_type):
    monkeypatch.setenv("TAVILY_API_KEY", "configured-extractor")
    requests = mock_http(monkeypatch, lambda request: httpx.Response(200, content=body, headers={"Content-Type": content_type}))

    async def forbidden_extract(url):
        pytest.fail("Lossy extractor must not hide raw fetch failure")

    monkeypatch.setattr(service, "call_tavily_extract", forbidden_extract)
    result = await service.fetch("https://example.org/code.h")
    assert result["ok"] is False
    assert result["provider"] == "direct"
    assert result["error_type"] == error_type
    assert result["content"] == ""
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [206, 404, 500])
async def test_http_partial_or_error_is_not_full_source(monkeypatch, status):
    mock_http(monkeypatch, lambda request: httpx.Response(status, content=b"partial"))
    result = await service.fetch("https://example.org/code.h")
    assert result["ok"] is False
    assert result["error_type"] == "network_error"
    assert "HTTP " + str(status) in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("declared", [False, True])
async def test_size_limit_rejects_without_returning_truncated_code(monkeypatch, declared):
    headers = {"Content-Length": "1000"} if declared else {"Content-Length": "0"}
    mock_http(monkeypatch, lambda request: httpx.Response(200, content=b"1234567890", headers=headers))
    with pytest.raises(ProviderCallError, match="no partial source"):
        await direct_fetch.fetch_raw_text("https://example.org/code.py", max_bytes=5)


@pytest.mark.asyncio
async def test_deadline_bounds_dns_as_well_as_http(monkeypatch):
    cancelled = asyncio.Event()

    async def stalled_dns(url):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(direct_fetch, "_public_address", stalled_dns)
    with pytest.raises(TimeoutError):
        await direct_fetch.fetch_raw_text("https://example.org/code.py", deadline=time.monotonic() + 0.02)
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_plain_web_page_still_uses_configured_extractor(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "configured-extractor")

    async def extract(url):
        assert url == "https://docs.example.org/manual.html"
        return "# Page\nBody"

    monkeypatch.setattr(service, "call_tavily_extract", extract)
    result = await service.fetch("https://docs.example.org/manual.html")
    assert result["provider"] == "tavily"
    assert result["content"] == "# Page\nBody"


@pytest.mark.parametrize("no_proxy", ["raw.githubusercontent.com", ".githubusercontent.com", "*"])
def test_proxy_bypass_is_evaluated_for_original_domain(monkeypatch, no_proxy):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    monkeypatch.setenv("NO_PROXY", no_proxy)
    assert direct_fetch._configured_proxy(httpx.URL("https://raw.githubusercontent.com/owner/repo/code.h")) is None


def test_proxy_auth_and_proxy_tls_are_separate_from_origin(monkeypatch):
    monkeypatch.setattr(direct_fetch.urllib.request, "getproxies", lambda: {"https": "https://user:password@proxy.example:8443"})
    bypassed = []
    monkeypatch.setattr(direct_fetch.urllib.request, "proxy_bypass", lambda host: bypassed.append(host) or False)
    proxy = direct_fetch._configured_proxy(httpx.URL("https://example.org/code.py"))
    assert bypassed == ["example.org"]
    assert proxy.raw_auth == (b"user", b"password")
    assert proxy.url.userinfo == b""
    assert proxy.ssl_context is not None
    assert not isinstance(proxy.ssl_context, direct_fetch._OriginTLSContext)
    assert proxy.ssl_context.check_hostname is True
    assert proxy.ssl_context.verify_mode == ssl.CERT_REQUIRED


def test_pinned_origin_tls_keeps_certificate_verification_and_original_sni(monkeypatch):
    received = {}

    def wrap_bio(self, incoming, outgoing, **kwargs):
        received.update(kwargs)
        return "wrapped"

    monkeypatch.setattr(ssl.SSLContext, "wrap_bio", wrap_bio)
    context = direct_fetch._OriginTLSContext("raw.githubusercontent.com")
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.wrap_bio(None, None, server_hostname="93.184.216.34") == "wrapped"
    assert received["server_hostname"] == "raw.githubusercontent.com"


@pytest.mark.asyncio
async def test_configured_proxy_keeps_ip_pin_and_no_credentials_in_origin_headers(monkeypatch):
    real_client = httpx.AsyncClient
    calls = []
    proxy = httpx.Proxy("http://proxy-user:proxy-secret@127.0.0.1:7897")

    async def address(url):
        return "93.184.216.34"

    def respond(request):
        assert request.url.host == "93.184.216.34"
        assert request.headers["Host"] == "example.org"
        assert "sni_hostname" not in request.extensions
        assert "proxy-authorization" not in request.headers
        assert "authorization" not in request.headers
        return httpx.Response(200, content=b"source\n")

    def client(**kwargs):
        calls.append(kwargs.copy())
        kwargs.pop("proxy")  # Mock the transport; do not connect to the real proxy.
        return real_client(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(direct_fetch, "_public_address", address)
    monkeypatch.setattr(direct_fetch, "_configured_proxy", lambda url: proxy)
    monkeypatch.setattr(httpx, "AsyncClient", client)
    result = await direct_fetch.fetch_raw_text("https://example.org/code.py")
    assert result["content"] == "source\n"
    assert calls[0]["trust_env"] is False
    assert calls[0]["proxy"] is proxy
    assert isinstance(calls[0]["verify"], direct_fetch._OriginTLSContext)
    assert calls[0]["verify"]._origin_hostname == "example.org"
