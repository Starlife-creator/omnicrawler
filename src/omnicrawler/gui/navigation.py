"""主导航行号常量（S3.1.2/15）——独立模块避免 delegates↔main 循环导入。"""

from __future__ import annotations


class NavIndex:
    """主导航列表行号（0-based）。替换魔法数字，杜绝"结果与复核"错页。

    取值必须与 main.py 中 nav_items 的逐行顺序一致；页面栈的映射由
    main._nav_pages 字典负责。
    """

    WORK_HEADER = 0
    HOME = 1
    WORKSPACE = 2
    # Backward-compatible alias for extensions/tests that still import the old name.
    WIZARD = WORKSPACE
    MONITOR = 3
    RESULTS = 4
    AUTOMATION_HEADER = 5
    CHANGE_MONITOR = 6
    TOOLS_HEADER = 7
    PDF_WORKBENCH = 8
    CONVERT_TOOL = 9  # B-4：ConvertX 格式互转工具
    SCENE = 10  # S4：场景管理面板
    LOGIN_SESSION = 11  # U3：登录会话（工具分组；headed 登录窗口 + 会话列表）
    ADVANCED_HEADER = 12
    YAML_EDITOR = 13
    EVIDENCE = 14
    PLUGIN_MARKET = 15
    DEVELOPER = 16

    HEADERS = frozenset({WORK_HEADER, AUTOMATION_HEADER, TOOLS_HEADER, ADVANCED_HEADER})
