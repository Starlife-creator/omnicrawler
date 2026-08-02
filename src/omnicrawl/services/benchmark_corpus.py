"""Versioned offline corpus catalog and site-capsule materializer."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

WEB_STRUCTURES = (
    "static_article", "static_list", "nested_sections", "spa_react", "spa_vue", "infinite_scroll", "modal_content",
    "shadow_dom", "iframe", "login_form", "session_cookie", "search_form", "faceted_search", "table", "cards",
    "wordpress", "drupal", "mediawiki", "discourse", "multilingual", "redirect_chain", "encoding_legacy",
)
INTERACTIONS = ("page_number", "next_button", "cursor", "load_more", "infinite", "date_range", "search", "login", "modal", "tab", "same_url")
API_PATTERNS = ("rest_offset", "rest_cursor", "rest_nested", "graphql_query", "graphql_cursor", "form_post", "json_lines", "sse", "websocket", "long_poll", "unstable_schema")
PDF_LAYOUTS = (
    "text", "scan", "table", "double_column", "rotated", "damaged", "mixed_text_scan", "forms", "footnotes", "headers",
    "toc", "embedded_fonts", "cjk", "rtl", "figures", "multi_page_table", "stamp", "handwriting", "low_contrast", "encrypted",
)
SECURITY_ATTACKS = ("zip_bomb", "zip_slip", "csv_formula", "ssrf", "dns_rebinding", "redirect_escape", "prompt_injection", "oversized_response", "credential_exfiltration", "plugin_escape")


@dataclass(frozen=True, slots=True)
class SiteCapsule:
    capsule_id: str
    category: str
    snapshot: str
    dom: str
    resources: tuple[str, ...]
    har: dict[str, object]
    actions: tuple[dict[str, str], ...]
    cookie_names: tuple[str, ...]
    expected: dict[str, object]
    quality: dict[str, float]
    simulated_delay_ms: int = 0
    simulated_failure: str = ""


def minimum_catalog() -> dict[str, tuple[str, ...]]:
    return {"web": WEB_STRUCTURES, "interactions": INTERACTIONS, "api": API_PATTERNS, "pdf": PDF_LAYOUTS, "security": SECURITY_ATTACKS}


def materialize_capsule(root: Path, capsule: SiteCapsule) -> Path:
    target = root / capsule.capsule_id
    target.mkdir(parents=True, exist_ok=True)
    (target / "page.html").write_text(capsule.snapshot, encoding="utf-8")
    (target / "dom.html").write_text(capsule.dom, encoding="utf-8")
    (target / "capsule.json").write_text(json.dumps(asdict(capsule), ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def validate_capsule(path: Path) -> tuple[str, ...]:
    errors = []
    for name in ("page.html", "dom.html", "capsule.json"):
        if not (path / name).is_file():
            errors.append(f"缺少 {name}")
    try:
        value = json.loads((path / "capsule.json").read_text(encoding="utf-8"))
        if any("password" in str(item).casefold() or "token" in str(item).casefold() for item in value.get("cookie_names", [])):
            errors.append("Cookie清单疑似包含凭据而非名称")
    except (OSError, json.JSONDecodeError):
        errors.append("capsule.json 无法解析")
    return tuple(errors)

