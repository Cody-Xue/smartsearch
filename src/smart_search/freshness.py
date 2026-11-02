"""Small, bounded freshness policy; discovered URLs remain untrusted candidates."""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import urljoin, urlsplit, urlunsplit


def freshness_policy(query: str) -> dict:
    text = query.casefold()
    timely = bool(re.search(r"\b(?:latest|newest|current|currently|today|recent|as\s+of)\b|最新|当前|现在|今日|今天|截至|截止", text))
    release = timely and bool(re.search(r"\b(?:releases?|versions?|stable|lts)\b|版本|正式版|稳定版|发行|发布日期", text))
    # Using a current SDK is not itself a request for release/version facts.
    usage = bool(re.search(r"\b(?:how\s+to|usage|use\b.*\bapi)\b|\bexamples?\s+(?:of|for)\b|如何使用|用法|示例", text))
    release = release and not (usage and not re.search(r"\b(?:which|what|when)\b|哪个|什么|哪天|发布日期", text))
    prerelease_terms = r"\b(?:pre[- ]?releases?|alpha|beta|rc|preview)\b|预发布|预览版|测试版|候选版"
    excludes_preview = bool(re.search(r"(?:exclude|excluding|without|排除|不要|不含).{0,80}(?:" + prerelease_terms + r")", text))
    asks_stable = bool(re.search(r"\b(?:stable|final|production)\b|稳定|正式版", text))
    mixed_release_request = not excludes_preview and asks_stable and bool(
        re.search(prerelease_terms, text) and
        re.search(r"\b(?:compare|comparison|versus|vs|and|or)\b|比较|对比|区别|差异|与|和", text))
    stable_only = release and not mixed_release_request and (excludes_preview or asks_stable)
    cutoff = ""
    match = re.search(r"(?:as\s+of|截至|截止)\s*(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", text)
    if match:
        try:
            cutoff = date(*(int(part) for part in match.groups())).isoformat()
        except ValueError:
            pass
    return {"required": timely, "release_facts": release, "stable_only": stable_only, "requested_cutoff": cutoff}


def requested_domains(query: str) -> set[str]:
    return {match.casefold().removeprefix("www.") for match in re.findall(
        r"(?<![a-z0-9@])(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}(?![a-z0-9.-])", query, re.I)}


def _url(value: str, base: str = "") -> str:
    try:
        parsed = urlsplit(urljoin(base, value))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return ""
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))
    except ValueError:
        return ""


def _preview_release(text: str) -> bool:
    return bool(re.search(r"\b(?:pre[- ]?releases?|alpha\d*|beta\d*|rc\d*|preview|release\s+candidate|unreleased|nightly)\b|预发布|预览版|测试版|候选版"
                          r"|\d+\.\d+(?:\.\d+)?[-.]?(?:alpha|beta|rc|dev|a|b)\d+", text, re.I))


def _dated_context_score(text: str) -> int:
    return 2 * bool(re.search(r"\b20\d{2}\b", text)) + bool(re.search(r"\d+\.\d+\.\d+", text))


def freshness_candidates(query: str, evidence: list[dict], attempted: set[str]) -> list[dict]:
    """Only search result URLs and links actually present in read pages are eligible.

    An explicit domain is a user preference, not an automatic authority verdict.
    Never manufacture a /downloads or /releases path from a hostname.
    """
    policy = freshness_policy(query)
    domains = requested_domains(query)
    candidates: dict[str, dict] = {}
    preview_urls: set[str] = set()
    read_urls = {_url(item.get("url", "")) for item in evidence if item.get("read")}
    # Newly read indexes can reveal a newer release absent from an earlier
    # cached page. Give their observed links room in the bounded candidate set.
    ordered = [item for item in evidence if not item.get("read")]
    ordered += [item for item in reversed(evidence) if item.get("read")]
    for item in ordered:
        source_url = _url(item.get("url", ""))
        if source_url and source_url not in attempted | read_urls:
            if policy["stable_only"] and _preview_release(item.get("title", "") + " " + source_url):
                preview_urls.add(source_url)
            else:
                candidates.setdefault(source_url, {"url": source_url, "title": item.get("title", ""),
                    "context": item.get("content", "")[:500], "origin": "discovery"})
        if not policy["release_facts"] or not item.get("read") or not source_url:
            continue
        content = item.get("content", "")
        for match in re.finditer(r"\[([^\]\n]{1,200})\]\(([^\s)]+)(?:\s+[^)]*)?\)", content):
            title, target = match.groups()
            url = _url(target, source_url)
            if not url or urlsplit(url).hostname != urlsplit(source_url).hostname or url in attempted | read_urls:
                continue
            if not re.search(r"release|download|version|changelog|发行|版本|发布|下载", title + " " + url, re.I):
                continue
            # A binary download is not a release detail page.
            if re.search(r"\.(?:zip|gz|xz|exe|msi|msix|dmg|pkg|whl|deb|rpm)(?:\?|$)", url, re.I):
                continue
            line_start = content.rfind("\n", 0, match.start()) + 1
            line_end = content.find("\n", match.end())
            row = content[line_start:line_end if line_end >= 0 else len(content)]
            # The row owns the version status. Adjacent rows can describe a
            # different prerelease and must not contaminate this candidate.
            context = row if len(row) <= 1000 else content[max(line_start, match.start()-200):min(line_end if line_end >= 0 else len(content), match.end()+300)]
            if policy["stable_only"] and _preview_release(title + " " + url + " " + context):
                preview_urls.add(url)
                continue
            previous = candidates.get(url, {})
            if (not previous or previous["origin"] == "discovery" or
                    previous.get("source_url") == source_url and _dated_context_score(context) > _dated_context_score(previous["context"])):
                candidates[url] = {"url": url, "title": title,
                    "context": context, "origin": "read_page_link", "source_url": source_url}
    def rank(item):
        parsed = urlsplit(item["url"])
        host = (parsed.hostname or "").removeprefix("www.")
        preferred = any(host == domain or host.endswith("." + domain) for domain in domains)
        index = bool(re.search(r"/(?:downloads?|releases?|versions?|changelog)/?$", parsed.path, re.I))
        return (bool(domains) and not preferred, not index, item["origin"] != "read_page_link")
    return sorted((item for item in candidates.values() if item["url"] not in preview_urls), key=rank)[:16]


def freshness_excerpt(content: str, query: str, limit: int = 6000) -> str:
    """Expose dated/versioned lines to the judge without pretending to read omissions."""
    if len(content) <= limit:
        return content
    policy = freshness_policy(query)
    years = set(re.findall(r"\b20\d{2}\b", query))
    lines = content.splitlines()
    scored = []
    for index, line in enumerate(lines):
        score = 2 * sum(year in line for year in years)
        score += 2 * bool(re.search(r"latest|current|release date|最新|发布日期|发布时间", line, re.I))
        if policy["release_facts"]:
            score += bool(re.search(r"\d+\.\d+(?:\.\d+)?", line))
            score += 2 * bool(re.search(r"\d+\.\d+\.\d+", line))
        if score:
            scored.append((-score, index))
    selected, chars = set(), 0
    for _, index in sorted(scored):
        window = range(max(0, index-1), min(len(lines), index+2))
        cost = sum(len(lines[i])+1 for i in window if i not in selected)
        if chars+cost > limit-300:
            continue
        selected.update(window)
        chars += cost
    if not selected:
        return content[:limit] + "\n[Freshness excerpt truncated; unseen text is not evidence.]"
    return "\n".join(lines[i] for i in sorted(selected)) + "\n[Selected dated/versioned passages; other page text omitted.]"
