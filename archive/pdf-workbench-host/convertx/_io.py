"""Conversion-local cancellation checks and atomic file replacement."""

from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class ConversionCancelledError(Exception):
    """The conversion stopped before its output was committed."""


def check_cancel(options: dict[str, Any]) -> None:
    should_stop = options.get("_should_stop")
    if should_stop is not None and should_stop():
        raise ConversionCancelledError("转换已取消")


@contextmanager
def atomic_output(path: Path, options: dict[str, Any]) -> Iterator[Path]:
    """Close writer handles inside the context, then replace the target.

    The final cancellation check is the commit boundary: requests observed
    after it do not change a successful commit into a cancelled result.
    """
    check_cancel(options)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=path.suffix, dir=path.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        yield temporary
        check_cancel(options)
        temporary.replace(path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            logging.getLogger(__name__).warning("无法清理转换临时文件 %s", temporary, exc_info=True)
