"""User-facing task drafting for the converged simple/professional experience."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

QUICK_INTENTS = {"save_page", "collect_section", "download_files", "monitor_changes"}


@dataclass(frozen=True, slots=True)
class QuickTaskDraft:
    url: str
    intent: str
    source_kind: str
    max_pages: int
    download_files: bool
    process_pdf: bool
    monitor_changes: bool
    output_formats: tuple[str, ...] = ("xlsx", "csv", "jsonl")
    decisions: tuple[str, ...] = ()
    editable_sections: tuple[str, ...] = (
        "访问范围", "字段内容", "附件与PDF", "变化监测", "输出格式", "资源预算"
    )
    warnings: tuple[str, ...] = ()
    # ── 需求语义的承载位置（走查 R3.3 / R4.1） ────────────────────────────────
    #
    # 实测（0.13.0）：把需求原文喂给 ``compile_natural_language`` 时，字段清单、
    # 「按价格从低到高排序」、「统计每个用户的帖子数」、「点击年份 2015 等待 AJAX」
    # **全部丢失且 ``warnings`` 为空** —— 而 ``confirmation()`` 却把「字段内容」
    # 列为可修改项。即**声明了但未接线**：需求语义没有承载位置，解析结果只能被丢掉。
    #
    # 这里给出承载位置。约定：**凡进入 `unsupported` 的，必须同时进入 `warnings`** ——
    # 做不到的事要说出来，而不是静默丢弃（这与 R1/R2 的"判据不得静默"是同一条原则）。
    fields: tuple[str, ...] = ()
    post_processing: tuple[str, ...] = ()
    unsupported: tuple[str, ...] = ()

    @property
    def requires_wizard(self) -> bool:
        return self.intent == "collect_section"

    def confirmation(self) -> dict[str, object]:
        # 「字段内容」此前是**声明了却没有承载位置**的可修改项；现在值恒为列表，
        # "是不是自动推断"单列一个键，避免同一个键出现"列表 or 字符串"两种类型。
        return {
            "访问范围": {"入口": self.url, "最多页面": self.max_pages},
            "采集方式": self.source_kind,
            "字段内容": list(self.fields),
            "字段来源": "需求指定" if self.fields else "自动推断",
            "后处理": list(self.post_processing),
            "附件": self.download_files,
            "PDF处理": self.process_pdf,
            "变化监测": self.monitor_changes,
            "输出": list(self.output_formats),
            "为什么这样设置": list(self.decisions),
            "当前不支持": list(self.unsupported),
            "可修改": list(self.editable_sections),
            "提醒": list(self.warnings),
            "必须先试跑": True,
        }


def draft_quick_task(url: str, intent: str) -> QuickTaskDraft:
    """Create a conservative, explainable draft without performing network I/O."""
    normalized = url.strip()
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https", "file"} or not parsed.netloc and parsed.scheme != "file":
        raise ValueError("请输入完整的 http(s) 地址或本地 file 地址")
    if intent not in QUICK_INTENTS:
        raise ValueError(f"不支持的快速任务类型: {intent}")

    section = intent == "collect_section"
    download = intent == "download_files"
    monitor = intent == "monitor_changes"
    max_pages = 30 if section else 1
    decisions = [
        "默认遵守 robots.txt，并限制在入口站点内",
        f"{'栏目任务需要发现链接' if section else '单页目标无需扩展范围'}，最多处理 {max_pages} 页",
        "先运行少量样本，确认后才允许全量执行",
    ]
    if download:
        decisions.append("目标是下载附件，因此启用常见办公文件与 PDF")
    if monitor:
        decisions.append("目标包含变化监测，因此保留原始版本并比较同址内容")
    return QuickTaskDraft(
        url=normalized,
        intent=intent,
        source_kind="crawl" if section else "static_html",
        max_pages=max_pages,
        download_files=download,
        process_pdf=download,
        monitor_changes=monitor,
        decisions=tuple(decisions),
    )


def advanced_rule_summary(passthrough: dict[str, object]) -> tuple[int, tuple[str, ...]]:
    """Summarise preserved advanced sections without exposing or deleting them."""
    ignored = {"config_version", "project", "source", "crawl", "extract", "download", "outputs"}
    active = tuple(sorted(key for key, value in passthrough.items() if key not in ignored and value not in ({}, [], None)))
    return len(active), active

