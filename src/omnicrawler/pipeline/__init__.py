"""Pipeline 子包：编排整个采集→解析→质控→导出流程。

``Pipeline`` 是九阶段流程编排器，``build_registry`` 构建组件注册表。
"""

from .core import Pipeline
from .registry import build_registry

__all__ = ["Pipeline", "build_registry"]
