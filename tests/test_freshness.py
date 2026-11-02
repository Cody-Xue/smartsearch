"""Current facts require live page evidence, not a cached discovery snippet."""

import pytest

from smart_search import service
from smart_search.freshness import freshness_candidates, freshness_excerpt, freshness_policy, requested_domains
from smart_search.jev import JevClient, filter_evidence
from smart_search.jev_search import available_channels, search as execute_search
from smart_search.provider_errors import ProviderCallError
import time


@pytest.mark.parametrize("query", [
    "截至 2026-11-02，Rust 的最新稳定版本和发布日期是什么？",
    "What is the most recent stable Node.js release as of 2026-11-02?",
    "CUDA 最新正式版发布日期是哪一天？",
    "Newest stable version of Zig?",
])
def test_release_facts_are_not_api_documentation(query, monkeypatch):
    monkeypatch.setenv("CONTEXT7_API_KEY", "test")
    monkeypatch.setenv("EXA_API_KEY", "test")
    assert freshness_policy(query)["release_facts"]
    assert not service.build_rules_route(query).docs_intent
    assert all(item["provider"] != "context7" for item in available_channels(service, query, []))


@pytest.mark.parametrize("query", [
    "React useEffect cleanup documentation",
    "How to use the latest Python version asyncio API?",
    "如何使用最新版本 React 的 useEffect API？",
])
def test_api_usage_keeps_context7(query, monkeypatch):
    monkeypatch.setenv("CONTEXT7_API_KEY", "test")
    assert not freshness_policy(query)["release_facts"]
    assert any(item["provider"] == "context7" for item in available_channels(service, query, []))


def test_cutoff_and_observed_same_site_links_only():
    query = "As of 2026-11-02 latest stable release on compiler.example.org"
    assert freshness_policy(query)["requested_cutoff"] == "2026-11-02"
    assert freshness_policy("截至 2026-02-31 最新版本")["requested_cutoff"] == ""
    source = {"url": "https://compiler.example.org/archive/", "title": "Archive", "read": False,
              "content": "[Releases](/releases/)"}
    candidates = freshness_candidates(query, [source], set())
    assert [item["url"] for item in candidates] == [source["url"]]
    source.update(read=True, content="[Release index](/releases/)\n[Release 9.2](https://elsewhere.example/releases/9.2)\n[Download](./file.zip)")
    candidates = freshness_candidates(query, [source], set())
    assert [item["url"] for item in candidates] == ["https://compiler.example.org/releases/"]
    assert candidates[0]["origin"] == "read_page_link"


def test_domains_support_chinese_boundaries_and_new_tlds_without_versions():
    assert requested_domains("请给example.org的发布页以及compiler.app、search.ai、new.tech。3.15.2") == {
        "example.org", "compiler.app", "search.ai", "new.tech"}


def test_newly_read_index_links_fit_before_old_release_archive_links():
    old_links = "\n".join(f"[Release 1.{i}](https://example.org/releases/1.{i}/)" for i in range(30))
    evidence = [
        {"url": "https://example.org/archive/", "content": old_links, "read": True},
        {"url": "https://example.org/releases/", "content": "[Release 9.2](/releases/9.2/)", "read": True},
    ]
    candidates = freshness_candidates("Latest stable version", evidence, set())
    assert candidates[0]["url"] == "https://example.org/releases/9.2/"


def test_fresh_link_context_replaces_stale_discovery_for_identical_url():
    detail = "https://example.org/releases/9.2/"
    candidates = freshness_candidates("Latest stable version as of 2026-11-02", [
        {"url": detail, "content": "planned 2026-12-01", "read": False},
        {"url": "https://example.org/releases/", "content": f"[Release 9.2]({detail}) | 2026-10-25", "read": True},
    ], set())
    assert candidates[0]["origin"] == "read_page_link"
    assert "2026-10-25" in candidates[0]["context"]
    assert "2026-12-01" not in candidates[0]["context"]


@pytest.mark.parametrize("query, stable_only", [
    ("Latest stable compiler version, exclude alpha, beta and RC", True),
    ("截至 2027-03-01 最新正式版，排除 alpha 和 beta", True),
    ("Compare the latest stable and beta versions", False),
    ("对比最新稳定版和 beta 版本", False),
    ("Latest stable and beta versions and their release dates", False),
    ("What are the latest beta and stable releases?", False),
    ("当前测试版和稳定版分别是什么？", False),
])
def test_stable_filter_respects_comparisons_and_explicit_exclusions(query, stable_only):
    assert freshness_policy(query)["stable_only"] is stable_only
    content = "| 10.0 pre-release | [Download](/releases/10.0/) | 2027-09-01 |\n" \
              "| 9.8 stable | [Download](/releases/9.8/) | 2027-02-01 |"
    candidates = freshness_candidates(query, [{"url": "https://compiler.example/releases/", "content": content, "read": True}], set())
    urls = {item["url"] for item in candidates}
    assert "https://compiler.example/releases/9.8/" in urls
    assert ("https://compiler.example/releases/10.0/" in urls) is not stable_only
    stable = next(item for item in candidates if item["url"].endswith("/9.8/"))
    assert "pre-release" not in stable["context"]


def test_release_link_keeps_own_dated_row_instead_of_generic_first_label():
    detail = "https://compiler.example/releases/9.8.2/"
    content = f"[Download latest Compiler]({detail})\n\n| [Compiler 9.8.2]({detail}) | 2027-02-17 |"
    candidates = freshness_candidates("Latest stable version as of 2027-03-01", [
        {"url": "https://compiler.example/releases/", "content": content, "read": True},
    ], set())
    assert candidates[0]["title"] == "Compiler 9.8.2"
    assert "2027-02-17" in candidates[0]["context"]


def test_research_display_surfaces_late_dated_evidence_without_mutating_original():
    query = "截至 2027-03-01，Compiler 最新稳定版和发布日期是什么？排除 beta。"
    detail = "https://compiler.example/releases/9.8.2/"
    body = "Navigation menu: Home About Community\n" * 400 + \
           f"## Stable releases\n| [Compiler 9.8.2]({detail}) | 2027-02-17 | stable |\n" + \
           "Ancient history\n" * 400
    item = {"url": "https://compiler.example/releases/", "provider": "direct", "content": body,
            "content_len": len(body), "sha256": "original-body-hash", "truncated": False}
    before = dict(item)
    rendered = service._evidence_only_synthesis(query, [item], [])
    assert "Compiler 9.8.2" in rendered
    assert "2027-02-17" in rendered
    assert detail in rendered
    assert "selected passages; full text in evidence_items" in rendered
    assert len(rendered) < 2400
    assert item == before


def test_date_excerpt_does_not_claim_unseen_text():
    content = "Navigation\n" * 2000 + "Release 9.2 | 2026-10-25\n" + "old notes\n" * 1000
    excerpt = freshness_excerpt(content, "Latest version as of 2026-11-02")
    assert "Release 9.2 | 2026-10-25" in excerpt
    assert "omitted" in excerpt


def configure(monkeypatch):
    for key in ("TYPESAFE_API_KEY", "EXA_API_KEY", "TAVILY_API_KEY", "CONTEXT7_API_KEY"):
        monkeypatch.setenv(key, "test")
    monkeypatch.setenv("SMART_SEARCH_INTENT_ROUTER", "jev")


@pytest.mark.asyncio
async def test_single_search_reads_index_then_release_and_preserves_cutoff(monkeypatch):
    configure(monkeypatch)
    calls, reads = [], []
    archive = "https://compiler.example.org/archive/"
    index = "https://compiler.example.org/releases/"
    detail = "https://compiler.example.org/releases/9.2/"

    async def discover(query, **kwargs):
        calls.append(query)
        return {"ok": True, "results": [{"url": archive, "title": "Official release archive", "content": "Cached: 9.1 is latest"}]}

    async def fetch(url):
        reads.append(url)
        return {archive: f"[Release index]({index})",
                index: f"[Release 9.2]({detail}) | 2026-10-25\n[Release 10.0](https://compiler.example.org/releases/10/) | 2026-12-01",
                detail: "Compiler 9.2 stable release. Release date: 2026-10-25."}[url]

    async def judge(self, state, questions, timeout):
        if "available_channels" in state:
            assert not any(item["provider"] == "context7" for item in state["available_channels"])
            scores = {f"channel_{i}": 0.99 if item["provider"] == "exa" else 0.01
                      for i, item in enumerate(state["available_channels"])}
        elif "pages" in state:
            assert state["freshness_policy"]["requested_cutoff"] == "2026-11-02"
            scores = {f"page_{i}": 0.99 if item["url"] == detail else 0.01
                      for i, item in enumerate(state["pages"])}
        else:
            assert all(item.get("read") for item in state["evidence"])
            assert any("2026-10-25" in item["content"] for item in state["evidence"])
            scores = {"useful": 0.99, "sufficient": 0.99}
        return {"answers": {key: {"type": "noul", "noul": scores.get(key, 0.01)} for key in questions}}

    monkeypatch.setattr(service, "exa_search", discover)
    monkeypatch.setattr(service, "call_tavily_extract", fetch)
    monkeypatch.setattr(JevClient, "_request", judge)
    result = await service.search("As of 2026-11-02, what is the latest stable Compiler version and release date on compiler.example.org?")
    assert result["ok"] and result["evidence_assessment"]["sufficient"]
    assert len(calls) == 1
    assert reads == [archive, index, detail]
    assert result["freshness_check"]["requested_cutoff"] == "2026-11-02"
    assert result["providers_used"] == ["exa", "tavily"]
    assert any(item["url"] == detail and item["read"] for item in result["sources"])


@pytest.mark.asyncio
@pytest.mark.parametrize("restricted", ["filter", "disabled"])
async def test_freshness_never_bypasses_provider_restrictions(monkeypatch, restricted):
    configure(monkeypatch)
    if restricted == "disabled":
        monkeypatch.setenv("TAVILY_ENABLED", "false")

    async def discover(query, **kwargs):
        return {"ok": True, "results": [{"url": "https://example.org/releases/", "content": "9.1 cached"}]}

    async def forbidden(*args, **kwargs):
        pytest.fail("Filtered/disabled extraction provider must not be called")

    async def judge(self, state, questions, timeout):
        assert "available_channels" in state
        scores = {f"channel_{i}": 0.99 if item["provider"] == "exa" else 0.01 for i, item in enumerate(state["available_channels"])}
        return {"answers": {key: {"type": "noul", "noul": scores.get(key, 0)} for key in questions}}

    monkeypatch.setattr(service, "exa_search", discover)
    monkeypatch.setattr(service, "call_tavily_extract", forbidden)
    monkeypatch.setattr(JevClient, "_request", judge)
    result = await service.search("Latest stable compiler release?", providers="exa" if restricted == "filter" else "auto")
    assert result["ok"]  # Useful discovery is still returned, never falsely verified.
    assert not result["evidence_assessment"]["sufficient"]
    assert result["freshness_check"]["status"] == "no_allowed_fetch_channel"
    assert result["source_warning"]


@pytest.mark.asyncio
async def test_jev_raw_fetch_keeps_exact_text_and_direct_provenance(monkeypatch):
    configure(monkeypatch)
    url = "https://raw.githubusercontent.com/owner/repo/main/code.h"
    content = "\n#include <assert.h>\r\n#define under_score 1\r\n"

    async def judge(self, state, questions, timeout):
        scores = {f"channel_{i}": 0.99 if item["provider"] == "tavily" and item["operation"] == "fetch" else 0.01
                  for i, item in enumerate(state["available_channels"])}
        return {"answers": {key: {"type": "noul", "noul": scores.get(key, 0)} for key in questions}}

    async def raw(*args, **kwargs):
        return {"ok": True, "url": url, "provider": "direct", "content": content,
                "content_format": "raw_text", "sha256": "hash", "byte_count": len(content), "truncated": False}, []

    monkeypatch.setattr(JevClient, "_request", judge)
    monkeypatch.setattr(service, "_run_web_fetch_fallback", raw)
    result = await service.search(url, providers="tavily")
    assert result["ok"]
    assert result["content"].endswith(content)
    assert result["providers_used"] == ["direct"]
    assert result["sources"][0]["provider"] == "direct"
    assert result["sources"][0]["sha256"] == "hash"


@pytest.mark.asyncio
async def test_freshness_judge_failure_is_visible_and_keeps_read_text(monkeypatch):
    configure(monkeypatch)

    async def discover(query, **kwargs):
        return {"ok": True, "results": [
            {"url": "https://example.org/one", "content": "Candidate 1"},
            {"url": "https://example.org/two", "content": "Candidate 2"},
        ]}

    async def fetch(url):
        return "Read current announcement"

    async def judge(self, state, questions, timeout):
        if "pages" in state:
            raise ProviderCallError("timeout", "test judgment timed out")
        scores = {f"channel_{i}": 0.99 if item["provider"] == "exa" else 0.01 for i, item in enumerate(state["available_channels"])}
        return {"answers": {key: {"type": "noul", "noul": scores.get(key, 0)} for key in questions}}

    monkeypatch.setattr(service, "exa_search", discover)
    monkeypatch.setattr(service, "call_tavily_extract", fetch)
    monkeypatch.setattr(JevClient, "_request", judge)
    result = await service.search("Latest official announcement")
    assert result["ok"] and result["degraded"] and result["fallback_used"]
    assert "rules" in result["routing_decision"]["router_engines_used"]
    assert not result["evidence_assessment"]["sufficient"]
    assert result["content"].index("Read current announcement") < result["content"].index("Candidate 1")
    assert "not fetched or independently verified" in result["content"]


@pytest.mark.asyncio
async def test_raw_source_filter_keeps_atomic_body_and_checksum(monkeypatch):
    configure(monkeypatch)
    settings = service.config.jev_settings()
    client = JevClient(settings, time.monotonic()+30)
    body = "#include <assert.h>\n\n" + "unrelated block\n" * 400
    item = {"id": "e1", "url": "https://example.org/code.h", "provider": "direct", "content": body,
            "content_format": "raw_text", "sha256": "full-body-checksum", "truncated": False}

    async def judge(state, questions, phase):
        return {f"group_{i}": 0.9 if any("assert.h" in unit["content"] for unit in group) else 0.0
                for i, group in enumerate(state["groups"])}

    monkeypatch.setattr(client, "evaluate", judge)
    filtered, info = await filter_evidence(client, "assert include", [item])
    assert filtered == [item]
    assert filtered[0]["content"] == body
    assert info["dropped_chunk_ids"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("single_pass", [True, False])
async def test_failed_direct_fetch_is_not_retried_under_other_provider_names(monkeypatch, single_pass):
    configure(monkeypatch)
    monkeypatch.setenv("JINA_API_KEY", "test")
    raw_calls = []
    url = "https://raw.githubusercontent.com/owner/repo/main/code.h"

    async def judge(self, state, questions, timeout):
        scores = {f"channel_{i}": 0.99 if item["operation"] == "fetch" else 0.01
                  for i, item in enumerate(state["available_channels"])}
        return {"answers": {key: {"type": "noul", "noul": scores.get(key, 0)} for key in questions}}

    async def raw(*args, **kwargs):
        raw_calls.append(args[0])
        return None, [{"provider": "direct", "error_type": "network_error", "error": "origin unavailable"}]

    async def empty(*args, **kwargs):
        return []

    monkeypatch.setattr(JevClient, "_request", judge)
    monkeypatch.setattr(service, "_run_web_fetch_fallback", raw)
    monkeypatch.setattr(service, "call_tavily_search", empty)
    result = await execute_search(url, validation="balanced", fallback="auto", providers="tavily,jina",
                                  timeout_seconds=30, single_pass=single_pass)
    assert raw_calls == [url]
    assert sum(item["provider"] == "direct" for item in result["provider_attempts"]) == 1
    assert not any(item["provider"] == "jina" for item in result["provider_attempts"])
    assert service._provider_health_status("jina").get("state") != "cooldown"
