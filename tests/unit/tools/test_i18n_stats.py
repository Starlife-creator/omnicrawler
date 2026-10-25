"""`tools/i18n_stats.py` 实测统计与 `tools/extract_i18n.py` 转义护栏（C-#4 / C-#27）。

门禁反向断言：统计口径只认 ``.pot`` / ``.po`` 实测条目；合成语言包覆盖率
必须落在预期值上，低于门禁阈值必须退出 1；``.pot`` 转义必须把换行、引号、
反斜杠压成合法字面量，否则模板会产生未闭合 ``msgid`` 行。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]


def _load(module_name: str, tool_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, tool_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


STATS = _load("_i18n_stats_under_test", _ROOT / "tools" / "i18n_stats.py")
EXTRACT = _load("_extract_i18n_under_test", _ROOT / "tools" / "extract_i18n.py")

_POT = """msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"

msgid "Hello"
msgstr ""

msgid "Settings"
msgstr ""

msgid "Exit"
msgstr ""

msgid "About"
msgstr ""
"""

_PO = """msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"

msgid "Hello"
msgstr "Hola"

msgid "Settings"
msgstr "Ajustes"

msgid "Exit"
msgstr ""

msgid "Stale"
msgstr "Obsolete"
"""


def _write(tmp_path: Path) -> tuple[Path, Path]:
    pot = tmp_path / "omnicrawler-gui.pot"
    po_dir = tmp_path / "en_US" / "LC_MESSAGES"
    po_dir.mkdir(parents=True)
    po = po_dir / "omnicrawler-gui.po"
    pot.write_text(_POT, encoding="utf-8")
    po.write_text(_PO, encoding="utf-8")
    return pot, po


def test_measure_counts_entries_translated_missing_and_stale(tmp_path: Path) -> None:
    pot, po = _write(tmp_path)
    stats = STATS.measure(pot, po)

    assert stats["pot_entries"] == 4
    assert stats["po_entries"] == 4
    assert stats["translated"] == 2
    assert stats["missing"] == 2
    assert stats["stale"] == 1  # "Stale" 已不在模板中
    assert stats["coverage"] == 50.0
    assert stats["coverage_ratio"] == 0.5


def test_check_passes_above_threshold_and_fails_below(tmp_path: Path) -> None:
    pot, po = _write(tmp_path)
    STATS.POT_PATH = pot
    STATS.PO_PATH = po

    assert STATS.main(["--check", "--min-coverage", "0.40"]) == 0
    assert STATS.main(["--check", "--min-coverage", "0.60"]) == 1


def test_check_reports_missing_pot(tmp_path: Path) -> None:
    missing = tmp_path / "absent.pot"
    po = tmp_path / "absent.po"
    try:
        STATS.measure(missing, po)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("缺少 .pot 时必须抛 FileNotFoundError，不得静默归零覆盖率")


def test_extract_escapes_newline_quote_backslash_and_tab() -> None:
    raw = 'line one\nline two\ttab "quoted" \\back'
    escaped = EXTRACT._escape_msgid(raw)

    assert "\n" not in escaped
    assert "\t" not in escaped
    assert '\\"' in escaped
    assert "\\n" in escaped
    assert "\\t" in escaped
    assert "\\\\" in escaped
    # 转回后必须与原文一致（先反斜杠再转义的顺序不能颠倒）。
    restored = (
        escaped.replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace('\\"', '"')
        .replace("\\\\", "\\")
    )
    assert restored == raw


def test_real_repo_i18n_stats_parse_without_error() -> None:
    """真实模板/语言包必须能被标准库解析（不抛异常即结构有效）。"""
    stats = STATS.measure()
    assert stats["pot_entries"] > 0
    assert stats["translated"] >= 0
    assert 0.0 <= stats["coverage_ratio"] <= 1.0
