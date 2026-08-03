"""配置数据模型模块。

定义爬虫配置的核心数据结构：CrawlConfig, FieldDef, DownloadConfig。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4


@dataclass
class FieldDef:
    """字段提取定义。"""

    name: str
    selector: str
    selector_type: Literal["css", "xpath", "jsonpath"] = "css"
    attribute: str | None = None  # 提取属性，如 'href'
    regex: str | None = None
    required: bool = False
    fallback_xpath: str | None = None

    def validate(self) -> list[str]:
        """校验单个字段定义的合法性。"""
        errors: list[str] = []
        if not self.name or not self.name.strip():
            errors.append("字段名不能为空")
        if not self.selector or not self.selector.strip():
            errors.append(f"字段 '{self.name}' 的选择器不能为空")
        if self.selector_type not in ("css", "xpath", "jsonpath"):
            errors.append(f"字段 '{self.name}' 的选择器类型无效: {self.selector_type}")
        if self.regex:
            try:
                re.compile(self.regex)
            except re.error as e:
                errors.append(f"字段 '{self.name}' 的正则表达式无效: {e}")
        return errors


@dataclass
class DownloadConfig:
    """下载配置。"""

    enabled: bool = False
    extensions: list[str] = field(default_factory=lambda: [".pdf", ".jpg"])
    output_dir: str = "downloads"

    def validate(self) -> list[str]:
        """校验下载配置。"""
        errors: list[str] = []
        if self.enabled:
            if not self.extensions:
                errors.append("下载已启用但未指定文件扩展名")
            if not self.output_dir or not self.output_dir.strip():
                errors.append("下载已启用但未指定输出目录")
        return errors


@dataclass
class CrawlConfig:
    """爬虫任务完整配置。"""

    # ---- 元数据 ----
    task_id: str = field(default_factory=lambda: str(uuid4()))
    project_name: str = field(default_factory=lambda: f"task_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    workspace: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    task_intent: str = "auto"
    task_description: str = ""

    # ---- 执行类型 ----
    runner_type: Literal["local", "remote"] = "local"

    # ---- 数据源 ----
    source_kind: str = "static_html"
    seed_urls: list[str] = field(default_factory=list)

    # ---- 爬取参数 ----
    max_pages: int = 10
    delay: float = 1.0
    concurrency: int = 2
    resource_profile: Literal["economy", "balanced", "performance"] = "balanced"
    pagination: dict | None = None  # {"type": "page", "parameter": "page", "start": 1, "end": 10}

    # ---- 增量抓取 ----
    incremental: bool = False
    since_date: str | None = None  # "2026-01-01"

    # ---- 提取字段 ----
    fields: list[FieldDef] = field(default_factory=list)

    # ---- 下载 ----
    download: DownloadConfig = field(default_factory=DownloadConfig)

    # ---- 业务筛选、处理、更新与输出 ----
    topic_include_any: list[str] = field(default_factory=list)
    topic_include_all: list[str] = field(default_factory=list)
    topic_exclude: list[str] = field(default_factory=list)
    keep_uncertain_topics: bool = True
    process_pdf: bool = False
    pdf_ocr: str = "auto"
    monitor_same_url: bool = False
    output_formats: list[str] = field(default_factory=lambda: ["jsonl", "csv", "xlsx"])
    ai_mode: str = "disabled"
    ai_provider: str = ""
    ai_base_url: str = ""
    ai_model: str = ""
    ai_api_key_ref: str = ""

    # ---- 页面快照 ----
    snapshot_mode: bool = False  # 保存完整页面快照（单文件 HTML，含 CSS/图片）

    # ---- AI 智能提取 ----
    extraction_mode: Literal["selector", "ai", "hybrid"] = "selector"  # 选择器 / AI / 混合
    ai_extraction_prompt: str | None = None  # 自定义 AI 提取 prompt
    ai_chunk_strategy: str = "auto"  # auto | heading | fixed_chunk
    ai_max_tokens_per_chunk: int = 4000

    # ---- HTTP 选项 ----
    user_agent: str = "OmniCrawler-GUI/1.1"
    respect_robots: bool = True

    # Raw template/config values that the five-step GUI does not edit. They are deep-merged
    # back on save so advanced pagination, plugins, sessions and processors are never lost.
    passthrough: dict[str, Any] = field(default_factory=dict, repr=False)

    def validate(self) -> list[str]:
        """校验完整配置，返回错误列表，空列表表示校验通过。

        必须检查：
        - 至少一个种子 URL
        - 字段可为空；为空时由内核自动提取标题、正文等通用内容
        - 所有选择器非空
        - max_pages > 0
        - 若增量模式开启，since_date 必须有值且格式合法
        - 所有字段名不能重复
        """
        errors: list[str] = []

        # 种子 URL
        valid_urls = [u for u in self.seed_urls if u and u.strip()]
        if not valid_urls:
            errors.append("至少需要一个种子 URL")

        # 字段
        if self.fields:
            field_names = []
            for f in self.fields:
                errors.extend(f.validate())
                field_names.append(f.name)
            if len(field_names) != len(set(field_names)):
                errors.append("字段名不能重复")

        # max_pages
        if self.max_pages <= 0:
            errors.append("最大页数必须大于 0")

        # 延迟
        if self.delay < 0:
            errors.append("请求延迟不能为负数")

        # 并发
        if self.concurrency < 1:
            errors.append("并发数至少为 1")
        if self.resource_profile not in ("economy", "balanced", "performance"):
            errors.append("资源模式必须是省电、均衡或全速")
        if self.pdf_ocr not in ("auto", "never", "paddle", "tesseract"):
            errors.append("PDF OCR 必须是自动、关闭、Paddle 或 Tesseract")
        if self.ai_mode not in ("disabled", "local", "cloud", "custom"):
            errors.append("AI 模式无效")
        if self.ai_mode != "disabled" and (not self.ai_base_url or not self.ai_model):
            errors.append("启用 AI 后需要填写 API 地址和模型名")
        if not self.output_formats:
            errors.append("至少选择一种输出格式")

        # 增量模式
        if self.incremental and self.since_date:
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", self.since_date):
                errors.append("起始日期格式无效，应为 YYYY-MM-DD")

        # 下载配置
        errors.extend(self.download.validate())

        return errors

    def __post_init__(self) -> None:
        if not self.workspace:
            self.workspace = f"work/{self.project_name}"

    def is_valid(self) -> bool:
        """快速检查配置是否有效。"""
        return len(self.validate()) == 0

    def has_placeholders(self) -> bool:
        """检查是否存在未替换的模板占位符。"""
        placeholder_pattern = re.compile(r"\{\{.*?\}\}")
        for url in self.seed_urls:
            if placeholder_pattern.search(url):
                return True
        for f in self.fields:
            if placeholder_pattern.search(f.selector):
                return True
        return False
