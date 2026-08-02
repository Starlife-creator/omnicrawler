"""国际化工具模块（gettext 风格）。

提供 `_()` 翻译函数，支持：
- gettext 编译的 .mo 翻译文件（生产环境）
- 直接返回中文原文（开发期默认，保证零配置可用）
- 运行时语言切换（通过 set_language()）

翻译文件存放路径：locale/<lang>/LC_MESSAGES/omnicrawler.mo
"""

from __future__ import annotations

import gettext as _gettext
import os
from pathlib import Path

_translation: _gettext.NullTranslations | None = None
_current_language: str = "zh_CN"
_localedir: Path | None = None


def _find_localedir() -> Path | None:
    """查找 locale 目录。优先从包路径下查找。"""
    here = Path(__file__).resolve().parent
    candidates = [
        here / "locale",
        here.parent / "locale",
        here.parent.parent / "locale",
    ]
    for candidate in candidates:
        if candidate.is_dir():
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
            "omnicrawler",
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
