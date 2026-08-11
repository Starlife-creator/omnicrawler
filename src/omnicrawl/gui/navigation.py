"""主导航行号常量（S3.1.2/15）——独立模块避免 delegates↔main 循环导入。"""

from __future__ import annotations


class NavIndex:
    """主导航列表行号。替换魔法数字，杜绝"结果与复核"错页。"""

    HOME = 0
    WIZARD = 1
    PDF_WORKBENCH = 2
    YAML_EDITOR = 3
    MONITOR = 4
    RESULTS = 5
    EVIDENCE = 6
    CHANGE_MONITOR = 7
    PLUGIN_MARKET = 8
    DEVELOPER = 9
