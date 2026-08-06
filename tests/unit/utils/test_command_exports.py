"""S1.5.4 消费方测试：commands __all__ 懒加载导入。

验证 from omnicrawl.commands import * 不报 AttributeError，且
field_suggest 别名等价 field.execute_field_suggest。
"""

from __future__ import annotations


def test_commands_star_import_has_all_symbols() -> None:
    namespace: dict[str, object] = {}
    exec("from omnicrawl.commands import *", namespace)  # noqa: S102
    for name in (
        "run_task", "run_status", "field", "init_project", "components",
        "template", "plan", "recovery", "security", "workspace", "schedule",
        "worker", "field_suggest",
    ):
        assert name in namespace, f"__all__ 缺少 {name}"


def test_field_suggest_alias_points_to_underlying() -> None:
    from omnicrawl import commands as c

    assert c.field_suggest is c.field.execute_field_suggest


def test_lazy_attr_invalid_raises_attribute_error() -> None:
    from omnicrawl import commands as c

    try:
        _ = c.definitely_not_a_command
    except AttributeError:
        pass
    else:
        raise AssertionError("未知子命令应报 AttributeError")
