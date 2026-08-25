from __future__ import annotations

import io
import json
import logging
import sys
from datetime import UTC, datetime
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
        from ..security.redaction import redact_url

        value = {
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_url(record.getMessage()),
        }
        for key in ("run_id", "request_id", "task_id", "stage", "url", "site"):
            if hasattr(record, key):
                # P9-A1（B05-024）：url/site 可能内嵌凭据（scheme://u:p@h），写 JSON 前脱敏
                value[key] = redact_url(str(getattr(record, key)))
        if record.exc_info:
            value["exception"] = self.formatException(record.exc_info)
        return json.dumps(value, ensure_ascii=False, default=str)


class _RedactingFormatter(logging.Formatter):
    """文本 formatter：对最终消息做 redact_url 脱敏。

    FINAL-S10：原实现只有 JsonFormatter 脱敏，文件日志（text formatter）
    明文落盘带凭据 URL——同一事件控制台干净、omnicrawler.log 泄漏。
    注意：父类 format() 会无条件用 getMessage() 覆写 record.message，
    因此必须把脱敏结果写入 record.msg 并清空 args，而非预写 message。
    """

    def format(self, record: logging.LogRecord) -> str:
        from ..security.redaction import redact_url

        record.msg = redact_url(record.getMessage())
        record.args = None
        return super().format(record)


def _text_formatter() -> logging.Formatter:
    return _RedactingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")


def _log_file_path() -> Path:
    from ..core.runtime_paths import portable_data_root
    return portable_data_root() / "logs" / "omnicrawler.log"


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
