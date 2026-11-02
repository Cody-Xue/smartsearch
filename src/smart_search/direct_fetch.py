"""Bounded, lossless retrieval for public raw text and source-code URLs.

HTML extraction services are deliberately not fallbacks here: Markdown conversion
can remove angle-bracket includes, escape identifiers, or normalize whitespace.
"""
from __future__ import annotations

import asyncio
import codecs
import hashlib
import ipaddress
from pathlib import PurePosixPath
import re
import socket
import ssl
import time
from urllib.parse import unquote
import urllib.request

import httpx

from .provider_errors import ProviderCallError


MAX_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 5
DEFAULT_TIMEOUT = 30.0
_RAW_HOSTS = {"raw.githubusercontent.com", "gist.githubusercontent.com"}
_TEXT_SUFFIXES = {
    ".c", ".h", ".cc", ".hh", ".cpp", ".hpp", ".cxx", ".hxx", ".cs",
    ".py", ".pyi", ".rs", ".go", ".java", ".kt", ".kts", ".swift",
    ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".css", ".scss",
    ".sh", ".bash", ".ps1", ".bat", ".cmd", ".sql", ".lua", ".rb",
    ".r", ".jl", ".tex", ".md", ".rst", ".txt",
    ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".xml", ".csv", ".tsv", ".diff", ".patch", ".lock", ".proto",
}
_TEXT_NAMES = {"dockerfile", "makefile", "cmakelists.txt", "license", "copying", ".gitignore"}
_TEXT_MIMES = {
    "application/json", "application/ld+json", "application/xml",
    "application/javascript", "application/x-javascript", "application/yaml",
    "application/toml", "application/sql", "application/octet-stream",
}


class _OriginTLSContext(ssl.SSLContext):
    """Verify the original hostname while CONNECT still targets the pinned IP.

    httpcore's HTTP proxy tunnel does not honor the sni_hostname extension.
    This origin-only context fixes its TLS name without changing trust checks
    or the separate TLS context used to connect to an HTTPS proxy itself.
    """

    def __new__(cls, hostname: str):
        return super().__new__(cls, ssl.PROTOCOL_TLS_CLIENT)

    def __init__(self, hostname: str):
        self._origin_hostname = hostname
        self.load_default_certs()

    def wrap_bio(self, incoming, outgoing, server_side=False, server_hostname=None, session=None):
        return super().wrap_bio(incoming, outgoing, server_side=server_side,
                               server_hostname=self._origin_hostname, session=session)


def _configured_proxy(url: httpx.URL) -> httpx.Proxy | None:
    # Use the user's existing environment/system proxy, including NO_PROXY for
    # the ORIGINAL hostname (not the IP substituted later for safe connection).
    proxies = urllib.request.getproxies()
    if urllib.request.proxy_bypass(url.netloc.decode("ascii")):
        return None
    value = proxies.get(url.scheme) or proxies.get("all")
    if not value:
        return None
    if "://" not in value:
        value = "http://" + value
    try:
        proxy_url = httpx.URL(value)
        # Proxy credentials belong only to the transport's CONNECT/auth layer.
        # Never copy them into origin request headers, redirects, or results.
        return httpx.Proxy(proxy_url, ssl_context=ssl.create_default_context() if proxy_url.scheme == "https" else None)
    except (ValueError, httpx.InvalidURL) as exc:
        raise ProviderCallError("parameter_error", "Invalid configured network proxy") from exc


def is_raw_text_url(url: str) -> bool:
    """Recognize explicit raw hosts and text-file paths, without fetching HTML pages."""
    try:
        parsed = httpx.URL(url)
    except (TypeError, httpx.InvalidURL):
        return False
    host = parsed.host.lower()
    path = PurePosixPath(unquote(parsed.path))
    if host in _RAW_HOSTS or "/-/raw/" in parsed.path:
        return True
    # Repository file views are HTML even if their last segment is .py.
    if host in {"github.com", "www.github.com"}:
        return False
    if "/-/blob/" in parsed.path or "/-/tree/" in parsed.path:
        return False
    if host in {"bitbucket.org", "www.bitbucket.org", "codeberg.org"} and "/src/" in parsed.path:
        return False
    return path.suffix.lower() in _TEXT_SUFFIXES or path.name.lower() in _TEXT_NAMES


async def _public_address(url: httpx.URL) -> str:
    """Resolve once and reject any non-public answer; callers connect to this IP."""
    if url.scheme not in {"http", "https"} or not url.host or url.userinfo:
        raise ProviderCallError("parameter_error", "Direct text fetch requires a public HTTP(S) URL without credentials")
    try:
        literal = ipaddress.ip_address(url.host)
        addresses = [literal]
    except ValueError:
        try:
            records = await asyncio.get_running_loop().getaddrinfo(
                url.host, url.port or (443 if url.scheme == "https" else 80), type=socket.SOCK_STREAM,
            )
            addresses = list(dict.fromkeys(ipaddress.ip_address(record[4][0]) for record in records))
        except (OSError, ValueError) as exc:
            raise ProviderCallError("network_error", "Could not resolve raw text host") from exc
    if not addresses or any(not address.is_global or address.is_multicast for address in addresses):
        raise ProviderCallError("parameter_error", "Direct text fetch refuses non-public network addresses")
    # IPv4 first avoids selecting an unreachable IPv6 route on dual-stack Windows.
    return str(sorted(addresses, key=lambda address: address.version)[0])


def _decode_content(body: bytes, content_type: str, url: httpx.URL) -> tuple[str, str]:
    mime = content_type.partition(";")[0].strip().lower()
    if mime in {"text/html", "application/xhtml+xml"} or (mime and not mime.startswith("text/") and mime not in _TEXT_MIMES and not mime.endswith(("+json", "+xml"))):
        raise ProviderCallError("quality_error", "Raw text URL returned HTML or a non-text content type")
    charset = re.search(r"charset\s*=\s*[\"']?([^;\s\"']+)", content_type, re.I)
    encoding = charset.group(1) if charset else "utf-8"
    try:
        # Strict decoding, no newline translation, no strip, and no Markdown pass.
        encoding = codecs.lookup(encoding).name
        text = body.decode(encoding, errors="strict")
        if text.encode(encoding, errors="strict") != body:
            raise ValueError("encoding is not round-trip safe")
    except (LookupError, UnicodeError, ValueError) as exc:
        raise ProviderCallError("parse_error", "Raw text encoding cannot be decoded losslessly") from exc
    if "\x00" in text:
        raise ProviderCallError("quality_error", "Raw text URL returned binary content")
    # Detect common login/error HTML even if a relay labels it text/plain.
    html_source = (url.host in _RAW_HOSTS and PurePosixPath(url.path).suffix.lower() in {".html", ".htm"})
    if not html_source and re.match(r"\s*(?:\ufeff)?\s*(?:<!doctype\s+html\b|<html\b)", text, re.I):
        raise ProviderCallError("quality_error", "Raw text URL returned an HTML document")
    return text, encoding


async def _fetch(url: str, max_bytes: int) -> dict:
    current = httpx.URL(url)
    for hop in range(MAX_REDIRECTS + 1):
        address = await _public_address(current)
        # Pin the validated address to prevent a second DNS lookup/rebinding.
        # Host and TLS SNI remain the original hostname; no provider credentials,
        # cookies, or authenticated clients are reused. Only the existing network
        # proxy is selected explicitly, before the hostname is replaced by an IP.
        target = current.copy_with(host=address, fragment=None)
        host = current.netloc.decode("ascii")
        proxy = _configured_proxy(current)
        verify = _OriginTLSContext(current.host) if proxy is not None and current.scheme == "https" else True
        async with httpx.AsyncClient(trust_env=False, proxy=proxy, verify=verify, follow_redirects=False, timeout=DEFAULT_TIMEOUT) as client:
            async with client.stream(
                "GET", target,
                headers={"Host": host, "Accept": "text/plain, application/json, application/xml, */*;q=0.1", "User-Agent": "smart-search/direct-text", "Accept-Encoding": "identity"},
                # CONNECT forwards request extensions into the proxy connection;
                # an origin SNI override there would break HTTPS proxy TLS.
                extensions={} if proxy is not None else {"sni_hostname": current.host},
            ) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location or hop == MAX_REDIRECTS:
                        raise ProviderCallError("network_error", "Raw text redirect limit reached or Location missing")
                    redirected = current.join(location)
                    if current.scheme == "https" and redirected.scheme != "https":
                        raise ProviderCallError("parameter_error", "Direct text fetch refuses HTTPS downgrade redirects")
                    current = redirected
                    continue
                if response.status_code != 200:
                    # Avoid exposing remote error bodies or a partial 206 as a full file.
                    error_type = "auth_error" if response.status_code in {401, 403} else "network_error"
                    raise ProviderCallError(error_type, f"Raw text fetch returned HTTP {response.status_code}")
                try:
                    declared_size = int(response.headers.get("content-length", "0"))
                except ValueError:
                    declared_size = 0
                if declared_size > max_bytes:
                    raise ProviderCallError("quality_error", f"Raw text exceeds the {max_bytes}-byte limit; no partial source returned")
                chunks = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    chunks.extend(chunk)
                    if len(chunks) > max_bytes:
                        raise ProviderCallError("quality_error", f"Raw text exceeds the {max_bytes}-byte limit; no partial source returned")
                body = bytes(chunks)
                content_type = response.headers.get("content-type", "")
                text, encoding = _decode_content(body, content_type, current)
                return {
                    "ok": True, "url": url, "final_url": str(current), "provider": "direct",
                    "content": text, "content_format": "raw_text", "content_type": content_type,
                    "encoding": encoding, "byte_count": len(body),
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "hash_scope": "decoded_response_body", "truncated": False,
                }
    raise ProviderCallError("network_error", "Raw text redirect limit reached")


async def fetch_raw_text(url: str, *, deadline: float | None = None, max_bytes: int = MAX_BYTES) -> dict:
    """Fetch the entire raw text or fail explicitly; never return a lossy excerpt."""
    timeout = DEFAULT_TIMEOUT if deadline is None else min(DEFAULT_TIMEOUT, deadline - time.monotonic())
    if timeout <= 0:
        raise ProviderCallError("timeout", "Raw text fetch deadline exhausted")
    return await asyncio.wait_for(_fetch(url, max_bytes), timeout=timeout)
