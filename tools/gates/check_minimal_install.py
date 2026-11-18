"""Smoke-test the base install without relying on optional feature extras."""

from __future__ import annotations

import os
import sys
import time

OPTIONAL_RUNTIME_ROOTS = frozenset(
    {
        "PIL",
        "PySide6",
        "bs4",
        "boto3",
        "crawl4ai",
        "cssselect",
        "curl_cffi",
        "cv2",
        "ddddocr",
        "duckdb",
        "httpx",
        "lxml",
        "numpy",
        "onnxruntime",
        "openpyxl",
        "opensearchpy",
        "paddle",
        "paddleocr",
        "patchright",
        "pdfplumber",
        "playwright",
        "psutil",
        "psycopg",
        "pyarrow",
        "pypdf",
        "pypdfium2",
        "pytesseract",
        "redis",
        "reportlab",
        "requests",
        "ruamel",
        "scrapy",
        "selectolax",
        "selenium",
        "websockets",
    }
)


def _loaded_optional_modules() -> list[str]:
    return sorted(name for name in sys.modules if name.split(".", 1)[0] in OPTIONAL_RUNTIME_ROOTS)


def main() -> int:
    started = time.perf_counter()
    import omnicrawler  # noqa: F401

    elapsed_ms = (time.perf_counter() - started) * 1000
    maximum_ms = float(os.environ.get("OMNICRAWLER_IMPORT_BUDGET_MS", "300"))
    if elapsed_ms > maximum_ms:
        raise SystemExit(
            f"base import took {elapsed_ms:.1f} ms; budget is {maximum_ms:.1f} ms"
        )
    loaded = _loaded_optional_modules()
    if loaded:
        raise SystemExit(f"base import loaded optional runtimes: {loaded}")

    from omnicrawler.cli import main as cli_main

    try:
        cli_main(["--help"])
    except SystemExit as exc:
        if exc.code not in (None, 0):
            raise SystemExit(f"CLI --help exited with {exc.code}") from exc
    loaded = _loaded_optional_modules()
    if loaded:
        raise SystemExit(f"CLI --help loaded optional runtimes: {loaded}")

    print(f"minimal install smoke passed; import={elapsed_ms:.1f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
