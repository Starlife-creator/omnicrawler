"""国际化工具模块（gettext 风格）。

提供 `_()` 翻译函数，支持：
- gettext 编译的 .mo 翻译文件（生产环境）
- 直接返回中文原文（开发期默认，保证零配置可用）
- 运行时语言切换（通过 set_language()）

翻译文件存放路径：locale/<lang>/LC_MESSAGES/omnicrawl-gui.mo
"""

from __future__ import annotations

import gettext as _gettext
import os
import sys
from pathlib import Path

# S4.3.2：domain 与 locale/ 目录下的 .pot/.po 统一（原 "omnicrawler" 与
# "omnicrawl-gui.pot" 不匹配——假语言包根因，切换语言永不生效）
DOMAIN = "omnicrawl-gui"

_translation: _gettext.NullTranslations | None = None
_current_language: str = "zh_CN"
_localedir: Path | None = None


def _has_domain(localedir: Path) -> bool:
    """True if *localedir* really holds translation files for DOMAIN.

    The locale tree is ``locale/<lang>/LC_MESSAGES/<domain>.mo|.po``, so we
    inspect the per-language subdirectories to avoid a false positive from an
    empty ``locale/`` folder."""
    if not localedir.is_dir():
        return False
    return any(
        (lang_dir / "LC_MESSAGES" / f"{DOMAIN}.mo").is_file()
        or (lang_dir / "LC_MESSAGES" / f"{DOMAIN}.po").is_file()
        for lang_dir in localedir.iterdir()
        if lang_dir.is_dir()
    )


def _find_localedir() -> Path | None:
    """查找 locale 目录（含 LC_MESSAGES/<DOMAIN>.mo|.po）。

    旧实现的三个候选（gui/locale、omnicrawl/locale 下两级）全都不存在——
    真实位置在仓库根的 ``locale/``（或打包后的 ``site-packages/omnicrawl/locale``），
    于是 ``_find_localedir`` 恒返回 None，切英文成了静默空操作（审查报告 S42）。
    现改为：从模块所在目录沿父链逐级向上找 ``locale/``，并校验其中确有本
    domain 的翻译文件，覆盖源码形态与打包形态。
    """
    here = Path(__file__).resolve().parent
    candidates: list[Path] = []
    for ancestor in here.parents:  # gui → omnicrawl → src → 仓库根 → ...
        candidates.append(ancestor / "locale")
        candidates.append(ancestor / "locales")
    # pip wheel 形态：data-files 安装到 share/omnicrawl/locale
    candidates.append(Path(sys.prefix) / "share" / "omnicrawl" / "locale")
    candidates.append(here / "locale")
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if _has_domain(candidate):
            return candidate
    return None


def set_language(lang: str = "zh_CN") -> None:
    """设置当前语言并重新加载翻译。

    Args:
        lang: 语言代码，如 "zh_CN"、"en_US"。
              若对应翻译文件不存在，则回退到直接返回原文。
    """
    global _translation, _current_language, _localedir

    _current_language = lang

    if _localedir is None:
        _localedir = _find_localedir()

    if _localedir is None:
        # 无 locale 目录，使用 NullTranslations（直接返回原文）
        _translation = _gettext.NullTranslations()
        return

    try:
        _translation = _gettext.translation(
            DOMAIN,
            localedir=str(_localedir),
            languages=[lang],
            fallback=True,
        )
    except (OSError, FileNotFoundError):
        _translation = _gettext.NullTranslations()


def _(text: str) -> str:
    """翻译函数。

    在生产环境中从 .mo 文件查找翻译；
    在开发期（无翻译文件）直接返回中文原文。

    Args:
        text: 需要翻译的文本字符串（通常为中文）。

    Returns:
        翻译后的字符串；若无翻译则返回原文。
    """
    if _translation is None:
        set_language(_current_language)
    assert _translation is not None
    return _translation.gettext(text)


def ngettext(singular: str, plural: str, n: int) -> str:
    """复数形式翻译。"""
    if _translation is None:
        set_language(_current_language)
    assert _translation is not None
    return _translation.ngettext(singular, plural, n)


def get_current_language() -> str:
    """返回当前语言代码。"""
    return _current_language


def get_available_languages() -> list[str]:
    """返回可用的语言列表。"""
    if _localedir is None:
        return [_current_language]
    langs = [_current_language]
    if _localedir.is_dir():
        for entry in _localedir.iterdir():
            if entry.is_dir() and (entry / "LC_MESSAGES").is_dir():
                langs.append(entry.name)
    return list(dict.fromkeys(langs))  # 去重保序


# 初始化默认语言
set_language(os.environ.get("OMNICRAWL_LANG", "zh_CN"))
