from .i18n import source_message
import re
from dataclasses import dataclass, field
from typing import Any
from .freshness import freshness_policy


ALLOWED_INTENT_ROUTER_MODES = {"jev", "local", "hybrid", "rules", "off"}


def normalize_router_mode(value: str) -> str:
    mode = value.strip().lower()
    if mode not in ALLOWED_INTENT_ROUTER_MODES:
        raise ValueError(source_message('Invalid SMART_SEARCH_INTENT_ROUTER: {0}. Supported values: {1}', mode, ', '.join(sorted(ALLOWED_INTENT_ROUTER_MODES))))
    return {"hybrid": "jev", "rules": "local", "off": "local"}.get(mode, mode)

DOCS_INTENT_KEYWORDS = {
    "api",
    "sdk",
    "library",
    "framework",
    "docs",
    "documentation",
    "reference",
    "react",
    "next.js",
    "vue",
    "python",
    "prisma",
    "langchain",
    "openai",
    "context7",
    "接口",
    "文档",
    "库",
    "框架",
    "函数",
    "参数",
    "配置",
    "接入",
}

CURRENT_INTENT_KEYWORDS = {
    "今天",
    "今日",
    "最新",
    "国内",
    "中国",
    "政策",
    "新闻",
    "实时",
    "刚刚",
    "当前",
    "现在",
    "本周",
    "本月",
    "战报",
    "比分",
    "赛程",
    "赛果",
    "季后赛",
    "比赛",
    "nba",
    "足球",
    "篮球",
    "today",
    "latest",
    "current",
    "realtime",
    "live",
    "recent",
}

ZH_CURRENT_INTENT_KEYWORDS = {
    "今天",
    "今日",
    "最新",
    "国内",
    "中国",
    "政策",
    "新闻",
    "实时",
    "刚刚",
    "当前",
    "现在",
    "本周",
    "本月",
    "战报",
    "比分",
    "赛程",
    "赛果",
    "季后赛",
    "比赛",
    "足球",
    "篮球",
}

FETCH_INTENT_KEYWORDS = {"http://", "https://"}

VERTICAL_INTENT_KEYWORDS = {
    "cve",
    "vulnerability",
    "vulnerabilities",
    "安全漏洞",
    "漏洞",
    "finance",
    "financial",
    "股票",
    "基金",
    "财报",
    "法律",
    "法规",
    "legal",
    "law",
    "academic",
    "论文",
    "paper",
    "repo",
    "repository",
    "github",
    "gitlab",
    "codebase",
    "code search",
    "code docs",
    "代码",
    "代码库",
    "开源仓库",
}

@dataclass
class IntentRouteResult:
    query: str
    intent_router_mode: str
    required_capabilities: list[str] = field(default_factory=list)
    intent_signals: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    router_engines_used: list[str] = field(default_factory=list)
    degraded: bool = False
    degraded_reason: str = ""
    reasons: list[str] = field(default_factory=list)
    docs_intent: bool = False
    zh_current_intent: bool = False
    web_current_intent: bool = False
    fetch_intent: bool = False
    supplemental_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "docs_intent": self.docs_intent,
            "zh_current_intent": self.zh_current_intent,
            "web_current_intent": self.web_current_intent,
            "fetch_intent": self.fetch_intent,
            "supplemental_paths": list(self.supplemental_paths),
            "intent_router_mode": self.intent_router_mode,
            "required_capabilities": list(self.required_capabilities),
            "intent_signals": dict(self.intent_signals),
            "confidence": self.confidence,
            "router_engines_used": list(self.router_engines_used),
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason,
            "reasons": list(self.reasons),
        }


def contains_any(query: str, keywords: set[str]) -> bool:
    q = query.lower()
    return any(keyword.lower() in q for keyword in keywords)


def contains_term(query: str, keywords: set[str]) -> bool:
    """Like contains_any, but Latin keywords match whole words (with a plural s or a version
    number), so "api" is not found in "capital" nor "repo" in "report". CJK keywords have no
    word boundaries and still match as substrings."""
    q = query.lower()
    for keyword in keywords:
        term = keyword.lower()
        if not term.isascii():
            if term in q:
                return True
        elif re.search(rf"(?<![a-z0-9]){re.escape(term)}(?:e?s)?(?![a-z])", q):
            return True
    return False


def extract_urls(query: str) -> list[str]:
    urls = []
    for match in re.findall(r"https?://[^\s<>\]\)\"'，。；！？、：）】》」』]+", query):
        cleaned = match.rstrip(".,;，。；)")
        if cleaned:
            urls.append(cleaned)
    return urls


def _ordered_capabilities(capabilities: set[str]) -> list[str]:
    order = ["docs_search", "web_search", "web_fetch", "vertical_search"]
    return [capability for capability in order if capability in capabilities]


def build_rules_route(
    query: str,
    *,
    validation_level: str = "",
    plan_intent_signals: dict[str, Any] | None = None,
    mode: str = "rules",
) -> IntentRouteResult:
    plan_intent_signals = plan_intent_signals or {}
    urls = extract_urls(query)
    docs_intent = bool(plan_intent_signals.get("docs_api_intent")) or contains_term(query, DOCS_INTENT_KEYWORDS)
    freshness = freshness_policy(query)
    if freshness["release_facts"]:
        docs_intent = False
    zh_current_intent = (
        plan_intent_signals.get("locale_domain_scope") == "china"
        or contains_any(query, ZH_CURRENT_INTENT_KEYWORDS)
    )
    web_current_intent = bool(
        zh_current_intent
        or plan_intent_signals.get("recency_requirement") in {"recent", "current"}
        or contains_any(query, CURRENT_INTENT_KEYWORDS)
        or freshness["required"]
    )
    fetch_intent = bool(plan_intent_signals.get("known_url")) or bool(urls) or contains_any(query, FETCH_INTENT_KEYWORDS)
    vertical_intent = contains_term(query, VERTICAL_INTENT_KEYWORDS)

    capabilities: set[str] = set()
    supplemental_paths: list[str] = []
    reasons: list[str] = []
    signal_scores: dict[str, float] = {}

    def add_capability(capability: str, reason: str, score: float) -> None:
        capabilities.add(capability)
        if capability not in supplemental_paths:
            supplemental_paths.append(capability)
        reasons.append(reason)
        signal_scores[capability] = max(signal_scores.get(capability, 0.0), score)

    if docs_intent:
        add_capability("docs_search", source_message('rules matched docs/API/library terms'), 0.82)
    if web_current_intent:
        add_capability("web_search", source_message('rules matched current/locale/news terms'), 0.84)
    if validation_level == "strict":
        add_capability("web_search", source_message('strict validation requires source reinforcement'), 0.72)
    if fetch_intent:
        add_capability("web_fetch", source_message('rules matched a known URL or fetch request'), 0.95 if urls else 0.78)
    if vertical_intent:
        add_capability("vertical_search", source_message('rules matched vertical-domain terms'), 0.72)

    confidence = max(signal_scores.values(), default=0.35)
    intent_signals: dict[str, Any] = {
        "docs_api_intent": docs_intent,
        "current_or_locale_intent": web_current_intent,
        "known_url": fetch_intent,
        "vertical_intent": vertical_intent,
        "strict_validation": validation_level == "strict",
        "rule_scores": signal_scores,
        "urls": urls,
        "freshness": freshness,
    }
    for key, value in plan_intent_signals.items():
        intent_signals.setdefault(key, value)
    return IntentRouteResult(
        query=query,
        intent_router_mode=mode,
        required_capabilities=_ordered_capabilities(capabilities),
        intent_signals=intent_signals,
        confidence=round(confidence, 3),
        router_engines_used=["rules"],
        reasons=reasons or ["rules found no supplemental capability need"],
        docs_intent=docs_intent,
        zh_current_intent=bool(zh_current_intent),
        web_current_intent=web_current_intent,
        fetch_intent=fetch_intent,
        supplemental_paths=_ordered_capabilities(set(supplemental_paths)),
    )
