"""OmniCrawler plugins subpackage.

B01-018：补再导出，保证旧路径 ``from omnicrawl.plugins import X`` 可用
（兼容重定向表被同名真实包短路后，顶层符号仍由本包自身提供）。
"""

from .plugins import PluginMetadata, Registry, load_local_plugins

__all__ = ["PluginMetadata", "Registry", "load_local_plugins"]
