"""OmniCrawler quality subpackage.

B01-018：补再导出，保证旧路径 ``from omnicrawl.quality import X`` 可用
（兼容重定向表被同名真实包短路后，顶层符号仍由本包自身提供）。
"""

from .quality import assess_record, assess_records

__all__ = ["assess_record", "assess_records"]
