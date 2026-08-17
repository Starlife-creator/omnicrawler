"""OmniCrawler GUI — PyQt6 可视化爬虫配置与管理工作台。

作为 omnicrawler 核心引擎的增强前端，提供：
- 五步配置向导
- YAML 实时编辑器
- 任务运行与监控
- 结果查看与图表分析

通过 ``omnicrawler-gui`` 命令或 ``python -m omnicrawler.gui`` 启动。
"""

from omnicrawler import __version__ as __version__
