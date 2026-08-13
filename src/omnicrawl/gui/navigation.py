"""主导航行号常量（S3.1.2/15）——独立模块避免 delegates↔main 循环导入。"""

from __future__ import annotations


class NavIndex:
    """主导航列表行号（0-based）。替换魔法数字，杜绝"结果与复核"错页。

    取值必须与 main.py 中 nav_items 的逐行顺序一致；页面栈的映射由
    main._on_nav_changed 的 pages 元组负责。
    """

    HOME = 0
    WIZARD = 1
    PDF_WORKBENCH = 2
    CONVERT_TOOL = 3  # B-4：ConvertX 格式互转工具
    YAML_EDITOR = 4
    MONITOR = 5
    RESULTS = 6
    EVIDENCE = 7
    SCENE = 8  # S4：场景管理面板
    CHANGE_MONITOR = 9
    PLUGIN_MARKET = 10
    DEVELOPER = 11
