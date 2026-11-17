"""格式常量与别名归一（无内部依赖，专供 core/catalog/concurrency 共享）。"""

from __future__ import annotations

from pathlib import Path

IMAGE_EXTENSIONS: tuple[str, ...] = (
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif",
)

_ALIASES = {"jpeg": ".jpg", "tif": ".tiff"}


def normalize_image_extension(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip().lower()
    if not text:
        return None
    if not text.startswith("."):
        text = f".{text}"
    text = _ALIASES.get(text.lstrip("."), text)
    return text if text in IMAGE_EXTENSIONS else None


def image_suffix(path: Path) -> str | None:
    return normalize_image_extension(path.suffix)


def is_image_pair(source: Path, target: Path, src_format: str | None, dst_format: str | None) -> bool:
    source_ext = normalize_image_extension(src_format) or image_suffix(source)
    target_ext = normalize_image_extension(dst_format) or image_suffix(target)
    return bool(source_ext and target_ext)


__all__ = [
    "IMAGE_EXTENSIONS",
    "image_suffix",
    "is_image_pair",
    "normalize_image_extension",
]
