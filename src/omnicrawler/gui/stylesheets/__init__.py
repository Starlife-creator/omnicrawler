"""模块化 QSS 构建器与设计令牌样式缓存。"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from . import _base, _buttons, _dialogs, _navigation, _tables


def build_stylesheet_sections(
    tokens: Any,
    *,
    scale: int,
    radius: dict[str, int],
    spacing: dict[str, int],
    font_family_ui: str,
    font_family_mono: str,
    scaled_font_px: Any,
    scaled_padding_px: Any,
) -> str:
    kwargs = {
        "scale": scale,
        "RADIUS": radius,
        "SPACING": spacing,
        "FONT_FAMILY_UI": font_family_ui,
        "FONT_FAMILY_MONO": font_family_mono,
        "scaled_font_px": scaled_font_px,
        "scaled_padding_px": scaled_padding_px,
    }
    return "\n".join(
        (
            _base.render(tokens, **kwargs),
            _navigation.render(tokens, **kwargs),
            _buttons.render(tokens, **kwargs),
            _dialogs.render(tokens, **kwargs),
            _tables.render(tokens, **kwargs),
        )
    )


__all__ = ["build_stylesheet_sections", "lru_cache"]
