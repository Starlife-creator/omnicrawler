"""模板加载器模块。

加载内置和用户自定义配置模板，支持版本检测和占位符处理。
"""

from __future__ import annotations

from pathlib import Path

from ...templates.template_catalog import TemplateCatalog, TemplateRecord
from .config_model import CrawlConfig


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
            "seed_url": "种子 URL",
            "domain": "目标域名",
            "item_selector": "列表项 CSS 选择器",
            "title_selector": "标题 CSS 选择器",
            "link_selector": "链接 CSS 选择器",
            "date_selector": "日期 CSS 选择器",
        }
        return descriptions.get(key, key.replace("_", " "))
