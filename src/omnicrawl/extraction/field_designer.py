from __future__ import annotations

import itertools
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# S2.5.14：大页面节点上限，防止冻结数分钟
MAX_ANALYZED_ELEMENTS = 50_000

_HASHY = re.compile(r"^(?:[a-f0-9]{8,}|[A-Za-z_-]*\d[A-Za-z0-9_-]{10,})$")
_NAME_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"价格|金额|price|amount|cost", re.I), "price"),
    (re.compile(r"日期|时间|发布|date|time|published", re.I), "date"),
    (re.compile(r"作者|来源|author|source|publisher", re.I), "author"),
    (re.compile(r"标题|名称|title|name|headline", re.I), "title"),
    (re.compile(r"地址|位置|address|location", re.I), "address"),
    (re.compile(r"电话|手机|phone|mobile|tel", re.I), "phone"),
    (re.compile(r"邮箱|email|e-mail", re.I), "email"),
    (re.compile(r"编号|代码|identifier|\bid\b|code", re.I), "identifier"),
)


@dataclass(frozen=True, slots=True)
class FieldCandidate:
    suggested_name: str
    text: str
    tag: str
    css: str
    xpath: str
    attribute: str | None
    score: float
    stable_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_html(html: str, *, limit: int = 200) -> list[FieldCandidate]:
    """Return ranked, human-readable field candidates from an HTML document."""

    try:
        from lxml import html as lxml_html
    except ImportError as exc:
        raise RuntimeError("Visual field analysis requires lxml; install .[html]") from exc
    if not html.strip():
        return []
    root = lxml_html.fromstring(html)
    tree = root.getroottree()
    candidates: list[FieldCandidate] = []
    seen: set[tuple[str, str | None]] = set()
    ignored = {"html", "body", "script", "style", "noscript", "svg", "path", "meta", "link"}
    for element in itertools.islice(root.iter(), MAX_ANALYZED_ELEMENTS):
        tag = str(getattr(element, "tag", "")).casefold()
        if not tag or tag in ignored or not isinstance(element.tag, str):
            continue
        text = re.sub(r"\s+", " ", " ".join(element.itertext())).strip()
        attribute = _content_attribute(element, tag)
        preview = str(element.get(attribute, "")).strip() if attribute else text
        if not preview or len(preview) > 500:
            continue
        css, reasons = _css_selector(element)
        key = (css, attribute)
        if key in seen:
            continue
        seen.add(key)
        score = _candidate_score(element, tag, preview, reasons, attribute)
        if score < 0.25:
            continue
        # S4.5 P3#153：阈值必须高于基础分（基础 0.25）才有效过滤；
        # 低于阈值=全候选通过=白过滤
        if score <= 0.25:
            continue
        candidates.append(
            FieldCandidate(
                _suggest_name(element, preview, tag, attribute),
                preview[:300],
                tag,
                css,
                tree.getpath(element),
                attribute,
                round(score, 4),
                tuple(reasons),
            )
        )
    candidates.sort(key=lambda item: (-item.score, len(item.css), item.suggested_name, item.text))
    return candidates[: max(0, limit)]


def analyze_url(url: str, *, timeout_seconds: float = 25.0, limit: int = 200) -> list[FieldCandidate]:
    """Safely download a public page and suggest fields without starting a browser."""

    from ..core.config import DEFAULTS, AppConfig
    from ..core.models import CrawlRequest
    from ..fetching.http_client import HTTPFetcher

    raw = json.loads(json.dumps(DEFAULTS))
    raw["project"] = {"name": "field_designer", "workspace": ".field_designer"}
    raw["source"] = {"kind": "static_html", "seeds": [url]}
    raw["http"]["timeout_seconds"] = timeout_seconds
    raw["http"]["retries"] = 1
    raw["http"]["delay_seconds"] = 0
    root = Path.cwd().resolve()
    config = AppConfig(root / ".field_designer.yaml", root, raw, root / ".field_designer")
    result = HTTPFetcher(config).fetch(CrawlRequest(url))
    encoding = "utf-8"
    content_type = result.headers.get("content-type", "")
    match = re.search(r"charset=([\w.-]+)", content_type, re.I)
    if match:
        encoding = match.group(1)
    try:
        html = result.body.decode(encoding, errors="replace")
    except LookupError:
        html = result.body.decode("utf-8", errors="replace")
    return analyze_html(html, limit=limit)


def _content_attribute(element: Any, tag: str) -> str | None:
    if tag == "a" and element.get("href"):
        return "href"
    if tag in {"img", "video", "audio", "source"} and element.get("src"):
        return "src"
    if tag == "time" and element.get("datetime"):
        return "datetime"
    if tag in {"input", "textarea"} and element.get("value"):
        return "value"
    return None


def _stable_token(value: str) -> bool:
    return bool(value and len(value) <= 80 and not _HASHY.fullmatch(value))


def _quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _css_selector(element: Any) -> tuple[str, list[str]]:
    tag = str(element.tag).casefold()
    element_id = str(element.get("id", ""))
    if _stable_token(element_id):
        return f'#{_quote(element_id)}', ["stable-id"]
    for attribute in ("data-testid", "data-test", "itemprop", "name", "aria-label"):
        value = str(element.get(attribute, ""))
        if _stable_token(value):
            return f'{tag}[{attribute}="{_quote(value)}"]', [f"stable-{attribute}"]
    classes = [value for value in str(element.get("class", "")).split() if _stable_token(value)]
    if classes:
        return tag + "".join(f".{value}" for value in classes[:2]), ["stable-class"]
    pieces: list[str] = []
    current = element
    while current is not None and len(pieces) < 4 and isinstance(getattr(current, "tag", None), str):
        current_tag = str(current.tag).casefold()
        parent = current.getparent()
        if parent is None:
            pieces.append(current_tag)
            break
        siblings = [child for child in parent if getattr(child, "tag", None) == current.tag]
        if len(siblings) > 1:
            current_tag += f":nth-of-type({siblings.index(current) + 1})"
        pieces.append(current_tag)
        current = parent
    return " > ".join(reversed(pieces)), ["structural-path"]


def _suggest_name(element: Any, text: str, tag: str, attribute: str | None) -> str:
    hints = " ".join(
        str(element.get(name, ""))
        for name in ("id", "class", "itemprop", "name", "aria-label", "data-testid")
    )
    combined = f"{hints} {text[:120]}"
    for pattern, name in _NAME_HINTS:
        if pattern.search(combined):
            return name
    if attribute == "href":
        return "link"
    if attribute == "src":
        return "image"
    if tag in {"h1", "h2", "h3"}:
        return "title"
    if tag == "time":
        return "date"
    token = re.sub(r"[^A-Za-z0-9_]+", "_", hints.casefold()).strip("_")
    return token[:40] or "field"


def _candidate_score(
    element: Any,
    tag: str,
    text: str,
    reasons: list[str],
    attribute: str | None,
) -> float:
    score = 0.25
    if reasons and reasons[0] != "structural-path":
        score += 0.35
    if tag in {"h1", "h2", "h3", "time", "a", "img", "td", "th", "address"}:
        score += 0.2
    if attribute:
        score += 0.1
    if 2 <= len(text) <= 160:
        score += 0.15
    if element.get("itemprop"):
        score += 0.2
    # S2.5.14：len(element) 为直接子节点数（O(1)），替代 iterdescendants 全树遍历（O(n²)）
    if len(element) > 8:
        score -= 0.3
    return max(0.0, min(1.0, score))
