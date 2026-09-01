"""TaskCanvas 纯逻辑缝（FINAL 长期债 #1 Phase C——Strangler 第一刀）。

从 task_canvas.py 抽出的与 Qt 无关的纯函数，作为后续渐进置换的接缝：
- field_fingerprint：试跑一致性校验的字段集指纹（PRD §2.2.3）
- selector_kind：XPath/CSS 判定（与 step3_fields.selector_kind 同语义）

UI 结构性拆分（五区子控件）待视觉回归基线建立后再行推进。
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Literal

from ..core.config_model import CrawlConfig, FieldDef


def field_fingerprint(fields: Iterable[FieldDef]) -> str:
    """字段指纹：字段名 + 选择器 + 类型的有序序列化 MD5。

    刻意只序列化 name/selector/selector_type 三元组——顺序无关的展示性
    属性（required/help 等）变更不应使试跑指纹失效。
    """
    parts = [
        f"{f.name}\x1f{f.selector}\x1f{f.selector_type}" for f in fields
    ]
    return hashlib.md5("\n".join(parts).encode("utf-8")).hexdigest()


def crawl_fingerprint(config: CrawlConfig) -> str:
    """生成会影响采集/提取结果的稳定指纹，排除名称、调度和交付设置。"""
    passthrough_keys = (
        "source", "crawl", "http", "browser", "pagination", "extract", "processors",
    )
    payload = {
        "seed_urls": config.seed_urls,
        "source_kind": config.source_kind,
        "max_pages": config.max_pages,
        "delay": config.delay,
        "concurrency": config.concurrency,
        "resource_profile": config.resource_profile,
        "pagination": config.pagination,
        "fields": [
            {
                "name": field.name,
                "selector": field.selector,
                "selector_type": field.selector_type,
                "attribute": field.attribute,
                "regex": field.regex,
                "required": field.required,
                "fallback_xpath": field.fallback_xpath,
            }
            for field in config.fields
        ],
        "download": {
            "enabled": config.download.enabled,
            "extensions": config.download.extensions,
        },
        "process_pdf": config.process_pdf,
        "pdf_ocr": config.pdf_ocr,
        "snapshot_mode": config.snapshot_mode,
        "extraction_mode": config.extraction_mode,
        "ai_extraction_prompt": config.ai_extraction_prompt,
        "ai_chunk_strategy": config.ai_chunk_strategy,
        "ai_max_tokens_per_chunk": config.ai_max_tokens_per_chunk,
        "respect_robots": config.respect_robots,
        "topics": {
            "any": config.topic_include_any,
            "all": config.topic_include_all,
            "exclude": config.topic_exclude,
            "keep_uncertain": config.keep_uncertain_topics,
        },
        "advanced": {
            key: config.passthrough[key]
            for key in passthrough_keys
            if key in config.passthrough
        },
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def selector_kind(selector: str) -> Literal["css", "xpath"]:
    """判断选择器是 XPath 还是 CSS（默认 css）；与 step3_fields.selector_kind 同语义。"""
    stripped = (selector or "").strip()
    if not stripped:
        return "css"
    # XPath 通常以 / .// ( @ [ 或 // 开头；CSS 选择器不会
    if stripped.startswith(("/", ".//", "(", "@", "//")) or "[@" in stripped:
        return "xpath"
    return "css"
