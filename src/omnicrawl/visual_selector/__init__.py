"""可视化选择器 — 浏览器内点选元素，自动生成 OmniCrawler 字段配置。

架构:
    Chrome 扩展（EasySpider 兼容）← WebSocket → visual_selector 服务器
                                                     ↓
                                            field_converter → OmniCrawler YAML

用法:
    python -m omnicrawl.visual_selector          # 启动 WebSocket 服务
    omnicrawl visual-select                      # CLI 入口
"""

from __future__ import annotations

from .field_converter import FieldConverter, SelectionToFieldSpec
from .server import VisualSelectorServer, start_server
from .similarity import SimilarityEngine, find_similar_elements, generate_common_xpath

__all__ = [
    "SimilarityEngine",
    "find_similar_elements",
    "generate_common_xpath",
    "FieldConverter",
    "SelectionToFieldSpec",
    "VisualSelectorServer",
    "start_server",
]
