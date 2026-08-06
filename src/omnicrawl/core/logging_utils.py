from __future__ import annotations

import io
import json
import logging
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

_KNOWN_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        value = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("run_id", "request_id", "task_id", "stage", "url", "site"):
            if hasattr(record, key):
                value[key] = getattr(record, key)
        if record.exc_info:
            value["exception"] = self.formatException(record.exc_info)
        return json.dumps(value, ensure_ascii=False, default=str)


def _text_formatter() -> logging.Formatter:
    return logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")


def _log_file_path() -> Path:
    from ..core.runtime_paths import portable_data_root
    return portable_data_root() / "logs" / "omnicrawl.log"


def _stderr_stream() -> io.TextIOWrapper | io.StringIO:
    return getattr(sys, "__stderr__", None) or io.StringIO()


def configure_logging(level: str, log_format: str = "text", *, file_logging: bool = True) -> None:
    # S1.1.2 后项（源B P2#64）：未知日志级别不再 AttributeError，fallback INFO。
    effective = _KNOWN_LEVELS.get(level.upper())
    if effective is None:
        effective = logging.INFO

    stderr_handler = logging.StreamHandler(_stderr_stream())
    stderr_handler.setFormatter(JsonFormatter() if log_format == "json" else _text_formatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(stderr_handler)
    root.setLevel(effective)

    if file_logging:
        try:
            log_path = _log_file_path()
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
            file_handler.setFormatter(_text_formatter())
            root.addHandler(file_handler)
        except OSError as exc:
            logging.getLogger(__name__).warning("无法创建文件日志: %s", exc)

    if effective is not _KNOWN_LEVELS.get(level.upper()):
        logging.getLogger(__name__).warning("未知日志级别 %r，回退到 INFO", level)
