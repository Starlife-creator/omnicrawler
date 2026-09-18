"""把一句中文需求编译成**可审阅的任务设置**（走查 R3.3）。

## 背景

既有的中文需求解析（``services/natural_language_task.compile_natural_language``）
此前**只有 GUI 可达**：命令行与自动化场景完全拿不到这一层，而 ``auto-analyze`` 只接受 URL、
根本没有"用户要什么"这一层。于是同一句需求在 GUI 能被理解、在 CLI 只能靠人手写配置。

实测（0.13.0）还发现该解析器会把需求里的**字段清单 / 排序 / 统计 / 交互**静默丢掉且
``warnings`` 为空 —— 本模块把 CLI 接上去的同时，也把那些语义接到 ``QuickTaskDraft`` 上
（字段 / 后处理 / 当前不支持项），并保证**做不到的事会说出来**。

## 约定

- **只做解析，不发起任何网络请求**（与 ``draft_quick_task`` 的既有约定一致）。
- 输出是给人审阅的确认单（``confirmation``）+ 机器可读的 ``task`` 块。
"""

from __future__ import annotations

from typing import Any

from ..services.natural_language_task import compile_natural_language


def compile_request(request: str, *, fallback_url: str = "") -> dict[str, Any]:
    """解析一句中文需求，返回可审阅的任务设置。

    Raises:
        ValueError: 需求为空。
    """
    text = (request or "").strip()
    if not text:
        raise ValueError(
            "需求不能为空：请传入一句中文需求，例如「抓取 https://example.com 的标题和价格，输出 CSV」"
        )
    draft = compile_natural_language(text, fallback_url=fallback_url)
    task = draft.task
    return {
        "request": draft.request,
        "mode": draft.mode,
        "topics": list(draft.topics),
        "schedule": draft.schedule,
        "ai_enhanced": draft.ai_enhanced,
        "task": {
            "url": task.url,
            "intent": task.intent,
            "source_kind": task.source_kind,
            "max_pages": task.max_pages,
            "output_formats": list(task.output_formats),
            "fields": list(task.fields),
            "post_processing": list(task.post_processing),
            "unsupported": list(task.unsupported),
            "download_files": task.download_files,
            "process_pdf": task.process_pdf,
            "monitor_changes": task.monitor_changes,
            "decisions": list(task.decisions),
            "warnings": list(task.warnings),
            "requires_wizard": task.requires_wizard,
        },
        "confirmation": task.confirmation(),
        "next_step": (
            "确认无误后：omnicrawler auto-analyze <URL> 生成采集配置"
            "（或 omnicrawler wizard 逐步填写），再 omnicrawler run --config <配置>。"
        ),
    }
