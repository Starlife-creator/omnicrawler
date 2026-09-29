"""多索引（#77 Phase 1）的 CLI：``plugins catalogs list|add|remove``。

设计要点（与聚合器同一套安全语义）：

- **list 不改文件**：未配置 ``plugins.catalogs`` 时也如实展示"隐式官方源"
  （由 ``catalog_url`` 派生），并标 ``implicit``——否则用户会以为"一个源都没有"。
- **add 先播种**：把当前**生效**的源写进 ``catalogs`` 之后再追加新源，
  绝不能因为"第一次显式配置"就把官方源弄丢（否则升级后市场突然只剩第三方源）。
- **remove 到底就清键**：删到只剩隐式官方源时删除 ``catalogs`` 键，回到兼容路径。
- ★ **写回会重排 YAML 且不保留注释**（沿用本仓 CLI 写配置的既有行为，见 cli/_main.py）；
  检测到原文件有注释时**明示告知**，不静默吞掉用户的东西。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from ..core.config import load_config
from ..plugins.market_client import trust_level

#: 官方策展源的默认优先级（与 config.plugin_catalogs 的兼容路径一致）
OFFICIAL_PRIORITY = 0


def _config_path(config_path: str) -> Path:
    resolved = Path(config_path).expanduser()
    if not resolved.is_file():
        raise FileNotFoundError(f"配置文件不存在: {resolved}")
    return resolved


def _load_raw(config_file: Path) -> dict[str, Any]:
    raw = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("配置文件顶层必须是映射")
    return raw


def _has_comments(text: str) -> bool:
    """粗判是否含注释（保守：只看行首到 # 之间是否全是空白）。"""
    return any(re.match(r"^\s*#", line) for line in text.splitlines())


def _save_raw(config_file: Path, data: dict[str, Any]) -> str:
    """写回配置；返回"给用户看的提示"（有注释时非空）。"""
    original = config_file.read_text(encoding="utf-8")
    config_file.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    if _has_comments(original):
        return (
            "注意：写回会重排 YAML 且**不保留注释**（本仓 CLI 写配置的既有行为）。"
            "若配置里有重要注释，请自行补回。"
        )
    return ""


def catalogs_list(*, config_path: str) -> tuple[dict[str, Any], int]:
    """列出当前生效的全部索引（含隐式官方源）。"""
    config_file = _config_path(config_path)
    config = load_config(str(config_file))
    raw = _load_raw(config_file)
    explicit = isinstance((raw.get("plugins") or {}).get("catalogs"), list)
    sources = []
    for source in config.plugin_catalogs:
        sources.append(
            {
                "url": source["url"],
                "kind": source["kind"],
                "trust": trust_level(source["kind"]),
                "priority": source["priority"],
                "enabled": source["enabled"],
                "custom_trust_root": bool(str(source.get("trust") or "").strip()),
                "implicit": not explicit,
            }
        )
    return {
        "status": "ok",
        "sources": sources,
        "explicit": explicit,
        "detail": (
            f"共 {len(sources)} 个索引"
            + ("（当前为隐式官方源：plugins.catalogs 未配置）" if not explicit else "")
        ),
    }, 0


def catalogs_add(
    *,
    config_path: str,
    url: str,
    kind: str = "community",
    trust: str = "",
    priority: int = 100,
) -> tuple[dict[str, Any], int]:
    """添加（或就地更新）一个索引；**先播种**当前生效的源。"""
    config_file = _config_path(config_path)
    url = url.strip()
    if not url:
        return {"status": "failed", "detail": "需要给出索引 URL"}, 2
    if kind not in ("curated", "community", "topic"):
        return {"status": "failed", "detail": f"kind 非法: {kind}"}, 2

    config = load_config(str(config_file))
    seeded = [
        {
            "url": s["url"],
            "trust": str(s.get("trust") or ""),
            "kind": s["kind"],
            "priority": s["priority"],
            "enabled": s["enabled"],
        }
        for s in config.plugin_catalogs
    ]
    for source in seeded:
        if source["url"] == url:
            source.update({"kind": kind, "priority": int(priority)})
            if trust:
                source["trust"] = trust
            break
    else:
        seeded.append(
            {
                "url": url,
                "trust": trust,
                "kind": kind,
                "priority": int(priority),
                "enabled": True,
            }
        )

    raw = _load_raw(config_file)
    raw.setdefault("plugins", {})
    if not isinstance(raw["plugins"], dict):
        return {"status": "failed", "detail": "plugins 段不是映射，拒绝改写"}, 2
    raw["plugins"]["catalogs"] = seeded
    note = _save_raw(config_file, raw)
    return {
        "status": "ok",
        "detail": (
            f"已添加索引 {url}（kind={kind}, priority={priority}）；"
            f"当前共 {len(seeded)} 个" + (f"；{note}" if note else "")
        ),
        "sources": seeded,
    }, 0


def catalogs_remove(*, config_path: str, url: str) -> tuple[dict[str, Any], int]:
    """移除一个索引；删到只剩隐式官方源时清掉 ``catalogs`` 键（回到兼容路径）。"""
    config_file = _config_path(config_path)
    url = url.strip()
    raw = _load_raw(config_file)
    plugins = raw.get("plugins")
    if not isinstance(plugins, dict) or not isinstance(plugins.get("catalogs"), list):
        return {
            "status": "failed",
            "detail": "当前没有显式配置的索引（plugins.catalogs 为空）",
        }, 2

    remaining = [s for s in plugins["catalogs"] if str(s.get("url") or "").strip() != url]
    if len(remaining) == len(plugins["catalogs"]):
        return {"status": "failed", "detail": f"未找到索引 {url}"}, 2

    config = load_config(str(config_file))
    official_url = config.plugin_catalog_url
    only_official_left = (
        len(remaining) == 1
        and str(remaining[0].get("url") or "").strip() == official_url
        and not str(remaining[0].get("trust") or "").strip()
    )
    if not remaining or only_official_left:
        plugins.pop("catalogs", None)
        note_scope = "（已回到隐式官方源）"
    else:
        plugins["catalogs"] = remaining
        note_scope = ""
    note = _save_raw(config_file, raw)
    return {
        "status": "ok",
        "detail": f"已移除索引 {url}{note_scope}" + (f"；{note}" if note else ""),
    }, 0
