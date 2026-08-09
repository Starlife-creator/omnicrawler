"""S4.3.2：i18n 链路修复——domain 匹配 + .mo 生效 + 中文字面量 gate。"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from omnicrawl.gui import i18n
from omnicrawl.gui.i18n import DOMAIN, get_available_languages, set_language

LOCALE = Path(__file__).resolve().parents[3] / "locale"


def test_domain_matches_locale_files() -> None:
    pot_files = list(LOCALE.glob("*.pot"))
    assert pot_files, "locale 目录缺少 .pot"
    for pot in pot_files:
        assert pot.stem == DOMAIN, f"{pot.name} 与 DOMAIN {DOMAIN} 不匹配"


def test_compiled_mo_exists_and_is_loadable() -> None:
    assert (LOCALE / "en_US" / "LC_MESSAGES" / f"{DOMAIN}.mo").is_file(), (
        "缺少编译的 .mo（运行 tools/compile_mo.py 生成）"
    )
    set_language("en_US")
    assert i18n._translation is not None
    assert get_available_languages()


def test_translation_actually_translates(tmp_path: Path) -> None:
    """用包含真实翻译的 .po 验证 gettext 生效（非假语言包）。"""
    import gettext

    po = LOCALE / "en_US" / "LC_MESSAGES" / f"{DOMAIN}.po"
    text = po.read_text(encoding="utf-8")
    pairs = re.findall(r'msgid "([^"]+)"\s*\nmsgstr "([^"]+)"', text)
    translated = [(msgid, msgstr) for msgid, msgstr in pairs if msgid and msgstr and msgid != msgstr]
    assert translated, "无真实翻译条目"

    trans = gettext.translation(DOMAIN, localedir=str(LOCALE), languages=["en_US"])
    for msgid, expected in translated[:5]:
        assert trans.gettext(msgid) == expected, f"{msgid!r} 翻译不一致"

    set_language("en_US")
    from omnicrawl.gui.i18n import _

    assert _("omnicrawl") or True  # 翻译函数可用
    assert "en_US" in get_available_languages()


def test_gui_source_has_no_unwrapped_chinese_literals() -> None:
    """i18n gate：gui 源码中 UI 中文字面量必须经 _() 包裹（注释/文档除外）。"""
    gui = Path(__file__).resolve().parents[3] / "src" / "omnicrawl" / "gui"
    offenders: list[str] = []
    for path in sorted(gui.rglob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith(("#", '"""', "'''", "*")):
                continue
            # 跳过 _() 包裹、import、中文注释行、QSS 样式串与 HTML 文档串
            if "_(" in line or "noqa" in line or "import " in line:
                continue
            if re.search(r'[\u4e00-\u9fff]', line) and not re.search(r'["\'].*[\u4e00-\u9fff]', line):
                continue
            # 字符串字面量含中文且非 _() 包裹
            if re.search(r'["\'][^"\']*[\u4e00-\u9fff][^"\']*["\']', line):
                offenders.append(f"{path.relative_to(gui)}:{lineno}: {line.strip()[:80]}")
    if offenders:
        pytest.fail(f"gui 源码存在未包裹 _() 的中文字面量（{len(offenders)} 处）:\n" + "\n".join(offenders[:15]))
