"""S4.3.2：i18n 链路修复——domain 匹配 + .mo 生效 + 中文字面量 gate。"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from omnicrawler.gui import i18n
from omnicrawler.gui.i18n import DOMAIN, get_available_languages, set_language

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
    from omnicrawler.gui.i18n import _

    # 未知 msgid 回退原文；已知中文 msgid 必须取到英文译文
    assert _("omnicrawler") == "omnicrawler"
    assert _("运行任务") == "Run task"
    assert "en_US" in get_available_languages()


#: 真正的 ``_()`` 翻译调用：前一个字符不能是单词字符或点号。
#: （否则 ``super().__init__(`` / ``self._value(`` 里的 ``_(`` 会被误当成包裹）
_WRAPPED_CALL = re.compile(r"(?<![\w.])_\(")


def scan_unwrapped_chinese_literals(source: str) -> list[tuple[int, str]]:
    """扫描源码，返回「含中文的字符串字面量且未经 _() 包裹」的 (行号, 内容)。

    跳过：注释、**三引号串**（docstring / QSS / HTML）、import 行、`_(...)` 包裹体。

    2026-09-11 修正：原实现只按**行首是否以三引号开头**来识别串，docstring 的**续行**
    因此逃过豁免——只要续行的中文里带引号，就会被当成 UI 字面量误报
    （实例：`core/view_base.py` 的模块 docstring）。现改为跟踪三引号串状态。
    """
    offenders: list[tuple[int, str]] = []
    in_triple = False
    wrap_depth = 0  # 跨行 _(...) 括号深度：>0 表示当前行位于 _() 多行包裹体内
    for lineno, line in enumerate(source.splitlines(), 1):
        stripped = line.lstrip()
        if in_triple:
            # 处于三引号串内部：仅在遇到闭合标记（出现次数为奇数）时退出
            if line.count('"""') % 2 or line.count("'''") % 2:
                in_triple = False
            continue
        if stripped.startswith("#"):
            continue
        # 行首三引号：单行成串直接跳过，未闭合则进入跨行串状态
        if stripped.startswith('"""') or stripped.startswith("'''"):
            marker = '"""' if stripped.startswith('"""') else "'''"
            if stripped.count(marker) % 2:
                in_triple = True
            continue
        # 赋值右侧开启的未闭合三引号（如 QSS/HTML 串）→ 进入跨行串
        if line.count('"""') % 2 or line.count("'''") % 2:
            in_triple = True
            continue
        if stripped.startswith("*"):
            continue
        if wrap_depth > 0:
            wrap_depth += line.count("(") - line.count(")")
            continue
        # 跳过 _() 包裹、import、noqa 行。
        # ★ 2026-09-11 修正漏报：原判定用子串 `"_(" in line`，于是**任何私有方法调用**
        #   （`super().__init__(`、`self._value(`、`self._set_value(` …）都含 `_(`，
        #   整行被当成「已包裹」而豁免——`super().__init__("帮助中心", parent)` 就是这样
        #   长期逃过门禁的（见 audit-20260805 §A-28）。改为要求 `_(` 是真正的调用：
        #   前一个字符不能是单词字符或点号。
        wrapped = _WRAPPED_CALL.search(line) is not None
        if wrapped or "noqa" in line or "import " in line:
            if wrapped:
                delta = line.count("(") - line.count(")")
                if delta > 0:
                    wrap_depth = delta
            continue
        if re.search(r'[\u4e00-\u9fff]', line) and not re.search(r'["\'].*[\u4e00-\u9fff]', line):
            continue
        # 字符串字面量含中文且非 _() 包裹
        if re.search(r'["\'][^"\']*[\u4e00-\u9fff][^"\']*["\']', line):
            offenders.append((lineno, line.strip()[:80]))
    return offenders


def test_gui_source_has_no_unwrapped_chinese_literals() -> None:
    """i18n gate：gui 源码中 UI 中文字面量必须经 _() 包裹（注释/文档除外）。"""
    gui = Path(__file__).resolve().parents[3] / "src" / "omnicrawler" / "gui"
    offenders: list[str] = []
    for path in sorted(gui.rglob("*.py")):
        for lineno, text in scan_unwrapped_chinese_literals(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(gui)}:{lineno}: {text}")
    if offenders:
        pytest.fail(f"gui 源码存在未包裹 _() 的中文字面量（{len(offenders)} 处）:\n" + "\n".join(offenders[:15]))


class TestI18nGateScanner:
    """门禁自身的判定必须有测试兜住——否则它会「静默漏报」或「误报到没人敢用」。"""

    def test_flags_plain_chinese_setText(self) -> None:
        source = 'label.setText("中文标题")\n'
        assert scan_unwrapped_chinese_literals(source) == [(1, 'label.setText("中文标题")')]

    def test_does_not_exempt_dunder_init_call(self) -> None:
        """★ 回归：`super().__init__(` 里的 `_(` 不是翻译调用（§A-28）。

        原判定用子串 `"_(" in line`，任何私有方法调用都含 `_(`，整行被当成「已包裹」
        而豁免——`super().__init__("帮助中心", parent)` 就这样长期逃过门禁。
        """
        source = '        super().__init__("帮助中心", parent)\n'
        assert scan_unwrapped_chinese_literals(source) == [
            (1, 'super().__init__("帮助中心", parent)')
        ]

    def test_does_not_exempt_private_method_call(self) -> None:
        source = '        self._value("中文标题")\n'
        assert scan_unwrapped_chinese_literals(source) == [(1, 'self._value("中文标题")')]

    def test_allows_translated_literal(self) -> None:
        assert scan_unwrapped_chinese_literals('label.setText(_("中文标题"))\n') == []

    def test_allows_multi_line_translation(self) -> None:
        source = 'label.setText(\n    _(\n        "中文标题"\n    )\n)\n'
        assert scan_unwrapped_chinese_literals(source) == []

    def test_ignores_comment(self) -> None:
        assert scan_unwrapped_chinese_literals('    # 这是"中文"注释\n') == []

    def test_ignores_docstring_continuation_with_quotes(self) -> None:
        # 原实现的正解：docstring 续行里带引号的中文曾造成误报
        # （实例 core/view_base.py:5 —— 即"资产齐全、继承路径缺失"）。
        source = '"""模块说明。\n\n即"资产齐全、继承路径缺失"。\n"""\n'
        assert scan_unwrapped_chinese_literals(source) == []

    def test_ignores_triple_quoted_stylesheet(self) -> None:
        source = 'QSS = """\nQLabel { color: "红"; }\n"""\n'
        assert scan_unwrapped_chinese_literals(source) == []

    def test_resumes_scanning_after_docstring(self) -> None:
        source = '"""说明\n含"中文引号"\n"""\nlabel.setText("未包裹")\n'
        offenders = scan_unwrapped_chinese_literals(source)
        assert offenders == [(4, 'label.setText("未包裹")')]

    def test_ignores_import_line(self) -> None:
        assert scan_unwrapped_chinese_literals('from a import "中文"  # noqa\n') == []
