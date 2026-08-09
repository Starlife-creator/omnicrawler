"""Shared compatibility helpers for the specialised PDF command-line entry points."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from ..pdfx.templates import DEFAULT_PDF_TEMPLATE


def add_config_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default=DEFAULT_PDF_TEMPLATE, help="PDF 项目配置或内置模板引用")


def add_run_arguments(parser: argparse.ArgumentParser, *, auto_prepare: bool = False) -> None:
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int)
    parser.add_argument("--skip-ocr", action="store_true")
    if auto_prepare:
        parser.add_argument("--no-auto-prepare", action="store_true")


def emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def report_error(exc: Exception) -> int:
    print(f"错误: {type(exc).__name__}: {exc}", file=sys.stderr)
    return 1
