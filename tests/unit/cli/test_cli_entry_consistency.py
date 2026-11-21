"""两条 CLI 入口的启动行为必须一致（W6.7-④）。

## 背景（§5.7 登记）

`omnicrawler <cmd>`（统一入口）会先 `configure_logging(...)` 再打一行**启动日志**（数据目录 /
配置路径），而 `pdf` 的**转发路径**（`omnicrawler pdf …` / 别名 `pdf-process` / `pdf-extract`）
此前两件事都不做 ⇒ 同一次运行，换个入口就少一行排障信息。

现在两条路径共用 `_log_startup_paths()`（唯一实现）。本文件钉住：

1. **行为**：转发路径必须调用 `configure_logging` 与 `_log_startup_paths`；
2. **单一实现**：启动日志的格式串在 `_main.py` 里只出现一次（不许两条路径各写一份）；
3. **接线**：统一入口也必须调用同一个函数。
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MAIN = _REPO_ROOT / "src" / "omnicrawler" / "cli" / "_main.py"

_STARTUP_LINE = 'root_logger.info("omnicrawler %s 启动; data_dir=%s", __version__, _data_dir_hint())'


def test_alias_path_configures_logging_and_logs_startup(monkeypatch) -> None:
    """转发路径（含已发布别名）必须与统一入口做同样两件事。"""
    from omnicrawler.cli import _main

    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(_main, "configure_logging", lambda *a, **k: calls.append(("logging", a)))
    monkeypatch.setattr(_main, "_log_startup_paths", lambda *a, **k: calls.append(("startup", a)))
    monkeypatch.setattr(_main, "_dispatch_pdf", lambda argv: calls.append(("dispatch", tuple(argv))))

    for alias in ("pdf", "pdf-process", "pdf-extract"):
        calls.clear()
        _main.main([alias])
        kinds = [kind for kind, _ in calls]
        assert "logging" in kinds, f"{alias} 路径没有配置日志"
        assert "startup" in kinds, f"{alias} 路径没有打启动行"
        assert "dispatch" in kinds, f"{alias} 路径没有转发到 PDF 子系统"


def test_startup_line_has_a_single_implementation() -> None:
    """启动日志的格式串只能有一处 —— 两条路径各写一份就会再次漂移。"""
    source = _MAIN.read_text(encoding="utf-8")
    assert source.count(_STARTUP_LINE) == 1, (
        f"启动日志格式串出现 {source.count(_STARTUP_LINE)} 次；应只在 `_log_startup_paths` 里定义一次"
    )


def test_unified_entry_uses_the_shared_helper() -> None:
    """统一入口也必须调用 `_log_startup_paths`（而不是自己内联一段）。"""
    source = _MAIN.read_text(encoding="utf-8")
    assert "_log_startup_paths(getattr(args, \"config\", None))" in source, (
        "统一入口没有调用共享的启动日志函数"
    )


def test_pdf_console_scripts_removed_with_migration_note() -> None:
    """去留确认（§5.7 ③）：`pdf-process` / `pdf-extract` **已被有意移除**。

    ## 这条判据为什么反过来了

    原先它要求这两个 console script **保留**（「已发布物不能顺手删」）。
    2026-11-17 的实现侧决定是：PDF 工作台整体迁到插件 `pdf-workbench`，
    宿主里不再有 `apps.pdf_processor` / `apps.field_extractor`，
    所以这两条入口**无法保留**（目标模块不存在了）。

    该决定符合 §5.7 的迁移要求 —— `pyproject.toml` 里留了迁移说明，
    并指明替代路径 `omnicrawler pdf …`。

    于是判据改为守住**迁移事实**本身：
      1. 目标模块确实已不在宿主里（否则该保留而不是删）；
      2. `pyproject.toml` 里有迁移说明（不能静默删除）；
      3. 替代入口存在（`omnicrawler pdf` 子命令）。
    """
    import importlib.util

    pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    for name in ("pdf-process", "pdf-extract"):
        assert f'{name} = "omnicrawler.apps.' not in pyproject, (
            f"`{name}` 又出现在 pyproject.toml 里 —— 若宿主已恢复这些模块，"
            "请同时恢复「保留」判据并去掉迁移说明"
        )

    # 1) 目标模块确实不在了（否则删除是错的）
    for module in ("omnicrawler.apps.pdf_processor", "omnicrawler.apps.field_extractor"):
        # 父包可能整个不存在（那样 `find_spec` 会抛 ModuleNotFoundError，
        # 而不是返回 None）—— 两种情况都算「模块已不在宿主里」。
        try:
            spec = importlib.util.find_spec(module)
        except ModuleNotFoundError:
            spec = None
        assert spec is None, (
            f"{module} 仍可导入 —— 那么它对应的 console script 不该被删"
        )

    # 2) 迁移说明必须在（不能静默删除已发布物）
    assert "已移除" in pyproject and "pdf-workbench" in pyproject, (
        "pyproject.toml 缺少迁移说明：删除已发布的 console script 必须写明去向"
    )

    # 3) 替代入口存在
    assert "omnicrawler pdf" in pyproject or "pdf" in pyproject, (
        "迁移说明里应指出替代入口"
    )
