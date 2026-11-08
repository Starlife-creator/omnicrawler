"""Verify that an independently installed feature extra is import-complete.

## 为什么每个 feature extra 都要有导入契约

`pyproject.toml` 里有 20 个功能 extra（外加 `dev` / `build` / `full` / `full-macos`）。
它们原先只有 9 个在 CI 里被**独立安装并导入**验证过，其余 11 个——包括 `browser`、
`selenium`、`gui`、`postgresql`、`search`、`ocr-*`——属于「声明了但没人真的装过」：
一旦某个 extra 漏了包或包名写错，只有在用户按 extra 安装时才炸。

本工具的判据是「**装了这个 extra，它承诺的模块就必须能 import**」。映射必须与
`pyproject.toml` 的 extras 一一对应，由
`tests/unit/utils/test_extra_install_contract.py` 反向断言（新增 extra 却不登记 ⇒ 红）。

## 哪些 profile 不进 CI matrix

`HEAVY_PROFILES` 里的 profile 有意**不**进 CI 的 `feature-install` matrix，理由逐项写明。
它们仍然是合法 profile（本地可跑 `python tools/check_extra_install.py <name>`），
只是没有远端证据——这一点必须显式记录，不能让它变成「看起来全覆盖」。
"""

from __future__ import annotations

import argparse
import importlib
import sys

# extra 名 → 该 extra 承诺可导入的顶层模块。
PROFILE_IMPORTS: dict[str, tuple[str, ...]] = {
    "html": ("bs4", "lxml", "cssselect", "selectolax"),
    "pdf": ("pypdfium2", "pdfplumber", "pypdf", "reportlab", "openpyxl"),
    "browser": ("playwright", "patchright"),
    "selenium": ("selenium",),
    "async-http": ("httpx",),
    "ai": ("aiohttp",),
    "tls": ("curl_cffi",),
    "streams": ("websockets",),
    "distributed": ("redis",),
    "scrapy": ("scrapy",),
    "ocr-tesseract": ("pytesseract", "PIL"),
    "ocr-paddle": ("paddleocr", "paddle", "numpy"),
    "ocr-captcha": ("ddddocr", "onnxruntime"),
    "crawl4ai": ("crawl4ai",),
    "gui": ("PySide6", "ruamel.yaml", "lxml", "openpyxl", "psutil", "requests"),
    "security": ("keyring", "cryptography"),
    "storage": ("boto3", "duckdb", "pyarrow"),
    "postgresql": ("psycopg",),
    "search": ("opensearchpy",),
    "document": ("docx", "pptx", "chardet"),
}

# 聚合型 extra：不是「装了就多一组能力」，而是别的 extra 的并集，故不单独验证。
AGGREGATE_EXTRAS: frozenset[str] = frozenset({"full", "full-macos", "dev", "build"})

# 有意不进 CI feature-install matrix 的 profile：理由逐项写明（见模块 docstring）。
HEAVY_PROFILES: dict[str, str] = {
    "ocr-paddle": "paddlepaddle wheel 单平台 100-200MB，装一次超过 CI 作业预算",
    "crawl4ai": "传递引入 litellm 等大依赖，且需真实浏览器运行时才有意义",
    "gui": "需 Qt 系统库（libegl/libopengl 等）；已由 test 作业的 [...,gui,...] 覆盖",
}


def check(profile: str) -> list[str]:
    modules = PROFILE_IMPORTS.get(profile)
    if modules is None:
        return [f"unknown feature profile: {profile}"]
    errors: list[str] = []
    for module in modules:
        try:
            importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001 - import smoke reports every broken runtime
            errors.append(f"{profile} cannot import {module}: {type(exc).__name__}: {exc}")
    return errors


def ci_matrix_profiles() -> tuple[str, ...]:
    """CI 应逐个独立安装的 profile：全部 feature extra 减去有理由不进的。"""
    return tuple(
        name for name in PROFILE_IMPORTS if name not in HEAVY_PROFILES
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("profile", choices=sorted(PROFILE_IMPORTS))
    args = parser.parse_args()
    errors = check(args.profile)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"feature extra {args.profile} import contract passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
