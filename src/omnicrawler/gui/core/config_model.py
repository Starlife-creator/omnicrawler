"""配置数据模型模块。

定义爬虫配置的核心数据结构：CrawlConfig, FieldDef, DownloadConfig。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from ...core.utils import user_agent as _user_agent
from ...templates.template_catalog import PLACEHOLDER_RE
from ..i18n import _


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
            errors.append(_("字段名不能为空"))
        if not self.selector or not self.selector.strip():
            errors.append(_(f"字段 '{self.name}' 的选择器不能为空"))
        if self.selector_type not in ("css", "xpath", "jsonpath"):
            errors.append(_(f"字段 '{self.name}' 的选择器类型无效: {self.selector_type}"))
        if self.regex:
            try:
                re.compile(self.regex)
            except re.error as e:
                errors.append(_(f"字段 '{self.name}' 的正则表达式无效: {e}"))
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
                errors.append(_("下载已启用但未指定文件扩展名"))
            if not self.output_dir or not self.output_dir.strip():
                errors.append(_("下载已启用但未指定输出目录"))
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
    # B-2 闸门逐 URL 模板覆盖：URL → 强制模板 id（空串或 key 缺失 = 用 Categorizer 自动推荐）
    per_url_template_overrides: dict[str, str] = field(default_factory=dict)

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
    # A16：版本号不再硬编码，随包版本自动更新
    user_agent: str = field(default_factory=lambda: _user_agent("GUI"))
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
            errors.append(_("至少需要一个种子 URL"))

        # 字段
        if self.fields:
            field_names = []
            for f in self.fields:
                errors.extend(f.validate())
                field_names.append(f.name)
            if len(field_names) != len(set(field_names)):
                errors.append(_("字段名不能重复"))

        # max_pages
        if self.max_pages <= 0:
            errors.append(_("最大页数必须大于 0"))

        # 延迟
        if self.delay < 0:
            errors.append(_("请求延迟不能为负数"))

        # 并发
        if self.concurrency < 1:
            errors.append(_("并发数至少为 1"))
        if self.resource_profile not in ("economy", "balanced", "performance"):
            errors.append(_("资源模式必须是省电、均衡或全速"))
        if self.pdf_ocr not in ("auto", "never", "paddle", "tesseract"):
            errors.append(_("PDF OCR 必须是自动、关闭、Paddle 或 Tesseract"))
        if self.ai_mode not in ("disabled", "local", "cloud", "custom"):
            errors.append(_("AI 模式无效"))
        if self.ai_mode != "disabled" and (not self.ai_base_url or not self.ai_model):
            errors.append(_("启用 AI 后需要填写 API 地址和模型名"))
        if not self.output_formats:
            errors.append(_("至少选择一种输出格式"))

        # 增量模式
        if self.incremental and self.since_date:
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", self.since_date):
                errors.append(_("起始日期格式无效，应为 YYYY-MM-DD"))

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
        """检查是否存在未替换的模板占位符。

        B02-026：从「仅扫 seed_urls + fields[].selector」升级为整棵配置树扫描，
        覆盖 source.params / http.headers / source.login / pagination / max_pages /
        extract.item_selector / browser.actions[*].selector / topic_include_any 及
        passthrough 内嵌段等全部字符串值。占位符形态复用 template_catalog 的
        PLACEHOLDER_RE（`{{identifier}}`），与模板声明同源，避免注释性 `{{...}}` 误报。
        """
        def _scan(value: Any) -> bool:
            if isinstance(value, str):
                return bool(PLACEHOLDER_RE.search(value))
            if isinstance(value, dict):
                return any(_scan(item) for item in value.values())
            if isinstance(value, (list, tuple)):
                return any(_scan(item) for item in value)
            return False

        return _scan(asdict(self))

    def prune_orphan_overrides(self) -> int:
        """清理 seed_urls 变更后残留的 per_url_template_overrides 孤儿键。

        覆盖键以精确 URL 匹配种子；种子被删除/改名后旧键不再生效，
        序列化前清理可避免 YAML 中残留无意义映射。

        Returns:
            被清理的孤儿键数量。
        """
        if not self.per_url_template_overrides:
            return 0
        seeds = set(self.seed_urls)
        orphan = [url for url in self.per_url_template_overrides if url not in seeds]
        if not orphan:
            return 0
        for url in orphan:
            del self.per_url_template_overrides[url]
        return len(orphan)
