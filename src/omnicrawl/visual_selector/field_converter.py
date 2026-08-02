"""字段转换器 — EasySpider 浏览器选择结果 → OmniCrawler field_spec。

处理流程:
    浏览器扩展选中元素 → WebSocket JSON → SelectionToFieldSpec → OmniCrawler YAML fields
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SelectedElement:
    """浏览器中用户选中的单个元素。"""
    xpath: str = ""
    all_xpaths: list[str] = field(default_factory=list)
    tag: str = ""
    text: str = ""
    link: str = ""
    image_src: str = ""
    attributes: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_extension_message(cls, data: dict[str, Any]) -> SelectedElement:
        """从 EasySpider 浏览器扩展消息中解析。"""
        xpath = data.get("xpath", "")
        all_xpaths = data.get("allXPaths", [])
        if isinstance(all_xpaths, str):
            all_xpaths = [all_xpaths]
        return cls(
            xpath=xpath,
            all_xpaths=list(all_xpaths),
            tag=data.get("tag", ""),
            text=str(data.get("text", data.get("content", "")))[:200],
            link=data.get("link", data.get("href", "")),
            image_src=data.get("src", ""),
            attributes=data.get("attributes", {}),
        )


@dataclass
class SelectionToFieldSpec:
    """将一组浏览器选中的元素转换为 OmniCrawler 字段规格。"""

    elements: list[SelectedElement] = field(default_factory=list)
    common_xpath: str = ""
    field_prefix: str = "field"

    def to_omnicrawl_fields(self) -> dict[str, Any]:
        """生成 OmniCrawler extract.fields 配置。"""
        if not self.elements:
            return {}

        # 为每个元素类型生成对应的字段定义
        fields: dict[str, Any] = {}

        # 用最短的 XPath 作为通用选择器
        selector = self._best_selector()
        if not selector:
            # 从所有 XPath 候选中最短的
            all_candidates: list[str] = []
            for el in self.elements:
                all_candidates.extend(el.all_xpaths)
            selector = min(all_candidates, key=len) if all_candidates else self.elements[0].xpath

        elem = self.elements[0]
        tag = elem.tag.upper() if elem.tag else ""

        # 根据元素类型决定字段属性
        if tag == "IMG":
            fields[f"{self.field_prefix}_图片地址"] = {
                "selector": selector,
                "attribute": "src",
                "desc": "图片地址",
            }
        elif tag == "A":
            fields[f"{self.field_prefix}_链接文本"] = {
                "selector": selector,
                "attribute": "text",
                "desc": "链接文本",
            }
            fields[f"{self.field_prefix}_链接地址"] = {
                "selector": selector,
                "attribute": "href",
                "desc": "链接地址",
            }
        elif tag in ("INPUT", "TEXTAREA", "SELECT"):
            fields[f"{self.field_prefix}_值"] = {
                "selector": selector,
                "attribute": "value",
                "desc": "输入值",
            }
        else:
            fields[f"{self.field_prefix}_文本"] = {
                "selector": selector,
                "attribute": "text",
                "desc": "元素文本内容",
            }

        # 添加示例值
        if self.elements:
            examples = [el.text[:80] for el in self.elements[:5] if el.text]
            if examples:
                for key in fields:
                    fields[key]["examples"] = examples
                    break  # 只给第一个字段加示例

        return fields

    def _best_selector(self) -> str:
        """从所有候选 XPath 中选出最优的通用选择器。"""
        if self.common_xpath:
            return self.common_xpath
        # 选最短的 XPath（通常是最通用的）
        candidates: list[str] = []
        for el in self.elements:
            for xp in el.all_xpaths:
                if xp and "contains(., '" not in xp:  # 排除含具体文本的 XPath
                    candidates.append(xp)
        return min(candidates, key=len) if candidates else ""

    def to_omnicrawl_yaml(self, seed_url: str = "") -> dict[str, Any]:
        """生成完整的 OmniCrawler 最小 YAML 配置。"""
        fields = self.to_omnicrawl_fields()
        config: dict[str, Any] = {
            "project": {"name": "visual_selector_task"},
            "source": {"kind": "browser", "seeds": [seed_url] if seed_url else ["https://example.com"]},
            "crawl": {"max_pages": 50},
            "http": {"user_agent": "OmniCrawler/2.1 (+bot)", "respect_robots": True},
            "extract": {"mode": "html", "fields": fields},
            "outputs": {"jsonl": True, "csv": True},
            "browser": {"engine": "playwright", "headless": True},
        }
        return config


class FieldConverter:
    """管理多轮选择交互，累积构建完整字段配置。"""

    def __init__(self) -> None:
        self._selections: list[SelectionToFieldSpec] = []
        self._seed_url: str = ""

    def set_seed_url(self, url: str) -> None:
        self._seed_url = url

    def add_selection(
        self,
        elements: list[dict[str, Any]],
        common_xpath: str = "",
        label: str = "",
    ) -> dict[str, Any]:
        """添加一轮选择结果，返回本轮生成的字段。"""
        parsed = [SelectedElement.from_extension_message(el) for el in elements]
        prefix = label or f"字段{len(self._selections) + 1}"
        spec = SelectionToFieldSpec(
            elements=parsed,
            common_xpath=common_xpath,
            field_prefix=prefix,
        )
        self._selections.append(spec)
        return spec.to_omnicrawl_fields()

    def merge_fields(self) -> dict[str, Any]:
        """合并所有轮选择的字段为一个完整 fields 配置。"""
        merged: dict[str, Any] = {}
        for spec in self._selections:
            merged.update(spec.to_omnicrawl_fields())
        return merged

    def to_yaml(self) -> dict[str, Any]:
        """生成最终 OmniCrawler 配置。"""
        config: dict[str, Any] = {
            "project": {"name": "visual_selector_task"},
            "source": {"kind": "browser", "seeds": [self._seed_url] if self._seed_url else ["https://example.com"]},
            "crawl": {"max_pages": 200},
            "http": {"user_agent": "OmniCrawler/2.1 (+bot)", "respect_robots": True},
            "extract": {"mode": "html", "fields": self.merge_fields()},
            "outputs": {"jsonl": True, "csv": True, "xlsx": True},
            "browser": {"engine": "playwright", "headless": True},
        }
        return config

    def clear(self) -> None:
        self._selections.clear()
        self._seed_url = ""
