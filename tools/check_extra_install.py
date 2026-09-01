"""Verify that an independently installed feature extra is import-complete."""

from __future__ import annotations

import argparse
import importlib
import sys

PROFILE_IMPORTS: dict[str, tuple[str, ...]] = {
    "html": ("bs4", "lxml", "cssselect", "selectolax"),
    "pdf": ("pypdfium2", "pdfplumber", "pypdf", "reportlab", "openpyxl"),
    "async-http": ("httpx",),
    "tls": ("curl_cffi",),
    "streams": ("websockets",),
    "storage": ("boto3", "duckdb", "pyarrow"),
    "security": ("keyring", "cryptography"),
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
