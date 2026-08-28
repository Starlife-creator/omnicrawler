from __future__ import annotations

import re
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
FUNCTION_EXTRAS = (
    "html",
    "pdf",
    "browser",
    "selenium",
    "async-http",
    "tls",
    "streams",
    "distributed",
    "scrapy",
    "ocr-tesseract",
    "ocr-paddle",
    "ocr-captcha",
    "crawl4ai",
    "gui",
    "security",
    "storage",
    "postgresql",
    "search",
    "document",
)


def _requirements_by_package(requirements: list[str]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for requirement in requirements:
        match = re.match(r"[A-Za-z0-9_.-]+", requirement)
        assert match is not None, f"无法解析依赖声明: {requirement}"
        name = match.group(0).lower().replace("_", "-")
        previous = resolved.setdefault(name, requirement)
        assert previous == requirement, f"同一依赖存在不一致约束: {previous!r} != {requirement!r}"
    return resolved


def test_full_extra_covers_all_function_extras() -> None:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = project["project"]["optional-dependencies"]
    expected: dict[str, str] = {}
    for extra_name in FUNCTION_EXTRAS:
        for package, requirement in _requirements_by_package(extras[extra_name]).items():
            previous = expected.setdefault(package, requirement)
            assert previous == requirement, (
                f"功能 extra 对 {package} 的约束不一致: {previous!r} != {requirement!r}"
            )
    # aiofiles 是 Full 构建链的显式附加依赖，尚无独立功能 extra。
    expected["aiofiles"] = "aiofiles>=25,<26"
    assert _requirements_by_package(extras["full"]) == expected


def test_macos_full_has_only_documented_platform_delta() -> None:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = project["project"]["optional-dependencies"]
    expected = _requirements_by_package(extras["full"])
    expected.pop("paddleocr")
    expected.pop("paddlepaddle")
    expected["opencv-contrib-python"] = "opencv-contrib-python>=4.10,<6"
    assert _requirements_by_package(extras["full-macos"]) == expected
