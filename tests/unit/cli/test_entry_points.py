"""入口收敛（P2-4）的契约测试。

`优化方案.md` 的 P2-4 要求把 6 个 console script 收敛为「统一子命令 + 别名」。
本文件锁定收敛后的四条性质，其中两条是针对**已经发生过**的漂移：

1. `pdf` 是 `omnicrawler` 的**真子命令**（出现在 `build_parser()` 里）——
   此前它只靠 `argv[0]` 嗅探分发，于是 `--help` 看不到它、CLI 文档契约也校验不到它。
2. 已发布的别名（`pdf-process` / `pdf-extract` / `workbench`）继续可用。
3. **每个 console script 都必须被 Dockerfile 装进镜像**——
   此前 Dockerfile 只 `COPY /usr/local/bin/omnicrawler*`，镜像里缺了 3 个 PDF 入口
   （审计 `report_packaging_scripts` 已记录，长期未被发现）。
4. 工作台入口与其它入口行为一致：`--help/--version` 有输出且**不开窗**，未知参数报错而不是静默忽略。
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import tomllib
from pathlib import Path

import pytest

from omnicrawler.cli._main import build_parser

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DOCKERFILE = _REPO_ROOT / "Dockerfile"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"

_SCRIPT_RE = re.compile(r"^COPY --from=builder (\S+)", re.MULTILINE)


def _top_level_commands() -> set[str]:
    parser = build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    return set()


def _console_scripts() -> dict[str, str]:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    return dict(data["project"]["scripts"])


def _dockerfile_bin_globs() -> list[str]:
    text = _DOCKERFILE.read_text(encoding="utf-8")
    return [match.rsplit("/", 1)[-1] for match in _SCRIPT_RE.findall(text)]


def test_pdf_is_a_real_subcommand() -> None:
    """`pdf` 必须在 parser 里，而不是只在嗅探集合里。"""
    assert "pdf" in _top_level_commands()


def test_pdf_help_is_listed_at_top_level(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--help"])
    assert excinfo.value.code == 0
    assert "pdf" in capsys.readouterr().out


def test_pdf_subcommand_forwards_args_to_the_pdf_subsystem(capsys) -> None:
    """`omnicrawler pdf --help` 转发给 PDF 子系统（拿到的是它的帮助，不是薄包装的）。"""
    from omnicrawler.cli._main import main

    with pytest.raises(SystemExit) as excinfo:
        main(["pdf", "--help"])
    assert excinfo.value.code == 0
    assert "pdf-core" in capsys.readouterr().out


def test_pdf_subcommand_forwards_nested_subcommand(capsys) -> None:
    from omnicrawler.cli._main import main

    with pytest.raises(SystemExit) as excinfo:
        main(["pdf", "doctor", "--help"])
    assert excinfo.value.code == 0
    assert "doctor" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("alias", "expected"),
    [("pdf-process", "pdf-process"), ("pdf-extract", "pdf-extract")],
)
def test_published_pdf_aliases_still_dispatch(alias: str, expected: str, capsys) -> None:
    """已发布的 console script 别名不能因为收敛而失效。"""
    from omnicrawler.cli._main import main

    with pytest.raises(SystemExit) as excinfo:
        main([alias, "--help"])
    assert excinfo.value.code == 0
    assert expected in capsys.readouterr().out


def test_workbench_subcommand_is_registered() -> None:
    assert "workbench" in _top_level_commands()


def test_every_console_script_is_shipped_in_the_docker_image() -> None:
    """★ 防再漂移：Dockerfile 必须覆盖 pyproject 里声明的**每一个**入口。

    此前 Dockerfile 只拷 `omnicrawler*`，`pdfx` / `pdf-process` / `pdf-extract`
    从未进入镜像——用户拉到的容器少一半命令，且没有任何测试会发现。
    """
    globs = _dockerfile_bin_globs()
    assert globs, "未从 Dockerfile 解析到任何 COPY .../bin 规则"
    missing = [
        name for name in _console_scripts() if not any(fnmatch.fnmatch(name, glob) for glob in globs)
    ]
    assert not missing, f"以下 console script 未被 Dockerfile 装入镜像: {sorted(missing)}"


def test_workbench_entry_accepts_help_and_does_not_open_a_window(capsys) -> None:
    """`--help` 必须直接给帮助（且不进入 Tk 主循环）。"""
    from omnicrawler.services.workbench import main as workbench_main

    with pytest.raises(SystemExit) as excinfo:
        workbench_main(["--help"])
    assert excinfo.value.code == 0
    assert "omnicrawler-workbench" in capsys.readouterr().out


def test_workbench_entry_reports_version(capsys) -> None:
    from omnicrawler.services.workbench import main as workbench_main

    with pytest.raises(SystemExit) as excinfo:
        workbench_main(["--version"])
    assert excinfo.value.code == 0
    assert "omnicrawler" in capsys.readouterr().out


def test_workbench_entry_rejects_unknown_arguments() -> None:
    """★ 收敛前这里是**静默忽略**并直接开窗——参数写错也不会有任何反馈。"""
    from omnicrawler.services.workbench import main as workbench_main

    with pytest.raises(SystemExit) as excinfo:
        workbench_main(["--definitely-not-an-option"])
    assert excinfo.value.code != 0


def test_workbench_handler_passes_no_argv() -> None:
    """`omnicrawler workbench` 不能把 "workbench" 漏进工作台的 argv（否则报未知参数）。"""
    from omnicrawler.cli import _handlers

    source = (_REPO_ROOT / "src" / "omnicrawler" / "cli" / "_handlers.py").read_text(encoding="utf-8")
    assert "workbench_main([])" in source
    assert _handlers.lookup("workbench") is not None
