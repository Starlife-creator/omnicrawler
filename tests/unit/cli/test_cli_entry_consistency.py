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


def test_pdf_aliases_remain_published_console_scripts() -> None:
    """去留确认（§5.7 ③）：`pdf-process` / `pdf-extract` 是**已发布**的 console script ⇒ 保留。

    这一条把「保留」的依据钉住：一旦有人从 `pyproject.toml` 里删掉它们，
    已发布物的入口就断了 —— 那时必须先有迁移策略，而不是顺手删掉。
    """
    pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for name in ("pdf-process", "pdf-extract"):
        assert f'{name} = "omnicrawler.apps.' in pyproject, (
            f"`pyproject.toml` 里少了已发布的 console script {name!r} —— "
            f"删除前必须有迁移策略（§5.7 按「已分发物需迁移」处理）"
        )
