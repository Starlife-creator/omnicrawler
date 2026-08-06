"""模板加载器模块。

加载内置和用户自定义配置模板，支持版本检测和占位符处理。
"""

from __future__ import annotations

import copy
from dataclasses import fields
from pathlib import Path

from ...templates.template_catalog import TemplateCatalog, TemplateRecord
from ..i18n import _
from .config_model import CrawlConfig, FieldDef


class TemplateInfo:
    """模板元信息。"""

    def __init__(
        self,
        name: str,
        filepath: Path,
        description: str = "",
        version: str = "1.0",
        is_builtin: bool = True,
        template_id: str = "",
        category: str = "legacy",
        tags: tuple[str, ...] = (),
        capabilities: tuple[str, ...] = (),
        verified_at: str = "",
        recommended_when: str = "",
        limitations: str = "",
        why: str = "",
    ) -> None:
        self.name = name
        self.filepath = filepath
        self.description = description
        self.version = version
        self.is_builtin = is_builtin
        self.template_id = template_id or name
        self.category = category
        self.tags = tags
        self.capabilities = capabilities
        self.verified_at = verified_at
        self.recommended_when = recommended_when
        self.limitations = limitations
        self.why = why

    @property
    def display_name(self) -> str:
        """显示名称。"""
        return self.name.replace("_", " ").title()


class TemplateLoader:
    """模板加载器。

    管理内置和用户自定义配置模板的发现、加载和版本检测。
    """

    def __init__(
        self,
        builtin_dir: Path,
        user_dir: Path | None = None,
        additional_builtin_dirs: tuple[Path, ...] = (),
    ) -> None:
        """初始化模板加载器。

        Args:
            builtin_dir: 内置模板目录路径。
            user_dir: 用户自定义模板目录路径（可选）。
        """
        self._builtin_dir = Path(builtin_dir)
        self._additional_builtin_dirs = tuple(Path(path) for path in additional_builtin_dirs)
        self._user_dir = Path(user_dir) if user_dir else None
        self._cache: dict[str, TemplateInfo] = {}

    def discover_templates(self, force: bool = False) -> list[TemplateInfo]:
        """发现所有可用模板。

        Args:
            force: 是否强制重新扫描（不使用缓存）。

        Returns:
            模板信息列表。
        """
        if self._cache and not force:
            return list(self._cache.values())

        by_id: dict[str, TemplateInfo] = {}

        # TemplateCatalog is the single parser for metadata, recursive discovery,
        # YAML extensions and stable user-overrides.  The GUI only adapts its DTO.
        for builtin_dir in (self._builtin_dir, *self._additional_builtin_dirs):
            for record in TemplateCatalog(builtin_dir).discover(refresh=force):
                by_id.setdefault(record.metadata.template_id, self._to_info(record))
        if self._user_dir:
            for record in TemplateCatalog(self._user_dir).discover(refresh=force):
                by_id[record.metadata.template_id] = self._to_info(record)

        templates = sorted(by_id.values(), key=lambda item: (item.category, item.display_name))
        self._cache = dict(by_id)
        return templates

    def load_template(self, name: str) -> CrawlConfig | None:
        """按名称加载模板并返回 CrawlConfig。

        Args:
            name: 模板名称（不含 .yaml 扩展名）。

        Returns:
            加载成功返回 CrawlConfig，失败返回 None。
        """
        info = self._cache.get(name)
        if info is None:
            # 尝试查找
            for template in self.discover_templates():
                if template.name == name or template.template_id == name:
                    info = template
                    break
        if info is None:
            return None
        try:
            from .config_serializer import load_yaml

            return load_yaml(info.filepath)
        except Exception:
            return None

    def extract_placeholders(self, config: CrawlConfig) -> dict[str, str]:
        """提取配置中的所有占位符及其默认值。

        Args:
            config: 爬虫配置对象。

        Returns:
            {占位符名称: 描述} 字典。占位符名称不含 {{}} 包裹。
        """
        placeholders: dict[str, str] = {}

        import re

        placeholder_pattern = re.compile(r"\{\{(\w+)\}\}")

        # 扫描 seed_urls
        for url in config.seed_urls:
            for match in placeholder_pattern.finditer(url):
                key = match.group(1)
                if key not in placeholders:
                    placeholders[key] = self._placeholder_description(key)

        # 扫描字段选择器
        for field in config.fields:
            for match in placeholder_pattern.finditer(field.selector):
                key = match.group(1)
                if key not in placeholders:
                    placeholders[key] = self._placeholder_description(key)

        return placeholders

    def get_template_info(self, name: str) -> TemplateInfo | None:
        """获取指定模板的元信息。"""
        direct = self._cache.get(name)
        if direct is not None:
            return direct
        return next((item for item in self._cache.values() if item.name == name), None)

    def combine(self, names: list[str]) -> CrawlConfig | None:
        """合并多个模板为一个可执行配置。

        Args:
            names: 模板名称列表，按顺序合并，后者覆盖前者。

        Returns:
            合并后的 CrawlConfig；任一模板缺失则抛出 ValueError。
        """
        if not names:
            raise ValueError(_(_("至少需要一个模板名称")))

        configs = []
        for name in names:
            config = self.load_template(name)
            if config is None:
                raise ValueError(_(_(f"模板不存在: {name}")))
            configs.append(config)

        merged = copy.deepcopy(configs[0])
        identity_fields = {"task_id", "created_at", "project_name", "workspace"}
        defaults = CrawlConfig()

        # seed_urls 合并去重
        seen_urls = set(merged.seed_urls)
        for config in configs[1:]:
            for url in config.seed_urls:
                if url not in seen_urls:
                    seen_urls.add(url)
                    merged.seed_urls.append(url)

        # fields 合并，冲突时后者覆盖
        field_by_name: dict[str, FieldDef] = {f.name: f for f in merged.fields}
        for config in configs[1:]:
            for field in config.fields:
                field_by_name[field.name] = copy.deepcopy(field)
        merged.fields = list(field_by_name.values())

        # 其余配置段取第一个模板；仅当后者显式设置（非默认值）且冲突时覆盖
        for config in configs[1:]:
            for dc_field in fields(CrawlConfig):
                name = dc_field.name
                if name in ("seed_urls", "fields") or name in identity_fields:
                    continue
                later = getattr(config, name)
                if later == getattr(defaults, name):
                    continue
                if later != getattr(merged, name):
                    setattr(merged, name, copy.deepcopy(later))

        return merged

    @staticmethod
    def _to_info(record: TemplateRecord) -> TemplateInfo:
        metadata = record.metadata
        return TemplateInfo(
            name=metadata.name,
            filepath=record.path,
            description=metadata.description,
            version=metadata.version,
            is_builtin=record.builtin,
            template_id=metadata.template_id,
            category=metadata.category,
            tags=metadata.tags,
            capabilities=metadata.capabilities,
            verified_at=metadata.verified_at,
            recommended_when=metadata.recommended_when,
            limitations=metadata.limitations,
            why=metadata.why,
        )

    @staticmethod
    def _placeholder_description(key: str) -> str:
        """根据占位符 key 返回人类可读的描述。"""
        descriptions = {
            "seed_url": _(_("种子 URL")),
            "domain": _(_("目标域名")),
            "item_selector": _(_("列表项 CSS 选择器")),
            "title_selector": _(_("标题 CSS 选择器")),
            "link_selector": _(_("链接 CSS 选择器")),
            "date_selector": _(_("日期 CSS 选择器")),
        }
        return descriptions.get(key, key.replace("_", " "))
