"""文件夹监听：下载/落地文件稳定后自动转换。

语义对齐成熟转换工具的 ``FolderWatcher``（只借行为，不复制源码）：

- **静置确认**：文件 mtime 距今超过 ``settle_seconds`` 才处理（Windows 默认 2s，
  其他平台 5s），避免下载中的半文件被转换；
- **独占打开探测**：先以读模式打开并读 1 字节，被其他进程独占时本轮跳过；
- **临时后缀过滤**：``.tmp/.part/.crdownload/.download/.partial/.swp/.lock``、
  ``~$``、``.`` 开头、``~`` 结尾；
- **去重**：``(size, mtime_ns)`` 签名；处理成功后记录，文件再次变化会重新处理；
- **输出目录排除**：目标目录位于源目录内时不扫描；目标与源同目录时记录自己写出的
  文件，避免回环转换；
- **全量重扫**：每轮都是完整扫描（不做增量游标），天然规避 watcher overflow 漏事件。
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .catalog import known_formats
from .output_rules import OutputRule

_TEMP_SUFFIXES = (
    ".tmp", ".part", ".crdownload", ".download", ".partial", ".swp", ".lock", ".bak",
)
_PLATFORM_SETTLE = 2.0 if os.name == "nt" else 5.0


def default_settle_seconds() -> float:
    return _PLATFORM_SETTLE


def _normalize_target_format(value: str) -> str:
    text = value.strip().lower()
    if not text:
        raise ValueError("ConvertX watch: 目标格式不能为空")
    if not text.startswith("."):
        text = f".{text}"
    if text == ".ndjson":
        text = ".jsonl"
    if text == ".db":
        text = ".duckdb"
    if text == ".jpeg":
        text = ".jpg"
    if text == ".tif":
        text = ".tiff"
    if text not in known_formats():
        raise ValueError(
            f"ConvertX watch: 未知目标格式 {value!r}；可用: {', '.join(known_formats())}"
        )
    return text


def _is_hidden_or_temp(path: Path) -> bool:
    name = path.name
    lower = name.lower()
    if (
        name.startswith(".")
        or name.startswith("~$")
        or name.endswith("~")
        or lower.endswith(_TEMP_SUFFIXES)
    ):
        return True
    try:
        attributes = int(getattr(path.stat(), "st_file_attributes", 0))
    except OSError:
        return False
    # FILE_ATTRIBUTE_HIDDEN = 0x2, FILE_ATTRIBUTE_SYSTEM = 0x4
    return bool(attributes & 0x6)


def _signature(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return int(stat.st_size), int(stat.st_mtime_ns)


def _probe_readable(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            handle.read(1)
    except (OSError, PermissionError):
        return False
    return True


@dataclass(frozen=True, slots=True)
class WatchConfig:
    source_dir: Path
    target_dir: Path
    target_format: str
    settle_seconds: float | None = None
    poll_interval: float = 1.0
    on_conflict: str = "rename"
    preserve_timestamps: bool = True
    recursive: bool = True
    max_files: int = 20_000
    exclude_dirs: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        if not self.source_dir.is_dir():
            raise ValueError(f"ConvertX watch: 源目录不存在: {self.source_dir}")
        if self.settle_seconds is not None and self.settle_seconds < 0:
            raise ValueError("ConvertX watch: settle_seconds 不能为负")
        if self.poll_interval <= 0:
            raise ValueError("ConvertX watch: poll_interval 必须 > 0")
        if self.max_files < 1:
            raise ValueError("ConvertX watch: max_files 必须 >= 1")
        target_format = _normalize_target_format(self.target_format)
        from .catalog import format_status

        if not format_status(target_format).writable:
            raise ValueError(
                f"ConvertX watch: 目标格式 {target_format} 当前不可写"
                f"（{format_status(target_format).reason or '缺少 Writer'}）"
            )
        object.__setattr__(self, "target_format", target_format)

    @property
    def effective_settle(self) -> float:
        return _PLATFORM_SETTLE if self.settle_seconds is None else float(self.settle_seconds)


@dataclass(slots=True)
class WatchEvent:
    source: Path
    status: str  # converted | skipped | error
    output: Path | None = None
    error: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)


Converter = Callable[[Path, Path, OutputRule], Any]


class FolderWatcher:
    """轮询式目录监听器；``scan_once`` 可单独用于 cron/测试。"""

    def __init__(self, config: WatchConfig, *, converter: Converter | None = None) -> None:
        self.config = config
        self._converter = converter or self._default_converter
        self._processed: dict[Path, tuple[int, int]] = {}
        source_resolved = config.source_dir.resolve()
        target_resolved = config.target_dir.resolve()
        # 目标目录与源目录相同时不能整体排除，否则一个文件都扫不到；
        # 此时依靠 _processed 记录自己写出的文件来防回环。
        excluded = list(config.exclude_dirs)
        if target_resolved != source_resolved:
            excluded.insert(0, config.target_dir)
        self._excluded = tuple(path.resolve() for path in excluded)

    @staticmethod
    def _default_converter(source: Path, target: Path, rule: OutputRule) -> Any:
        from ._core import convert

        return convert(source, target, output_rule=rule)

    def _is_excluded(self, path: Path) -> bool:
        try:
            resolved = path.resolve()
        except OSError:
            return True
        for excluded in self._excluded:
            if resolved == excluded or resolved.is_relative_to(excluded):
                return True
        return False

    def _iter_candidates(self) -> Iterator[Path]:
        if self.config.recursive:
            iterator = self.config.source_dir.rglob("*")
        else:
            iterator = self.config.source_dir.glob("*")
        count = 0
        for path in iterator:
            try:
                if not path.is_file():
                    continue
            except OSError:
                continue
            if _is_hidden_or_temp(path) or self._is_excluded(path):
                continue
            if path.suffix.lower() == self.config.target_format:
                continue
            count += 1
            if count > self.config.max_files:
                return
            yield path

    def _target_for(self, source: Path) -> Path:
        return self.config.target_dir / f"{source.stem}{self.config.target_format}"

    def scan_once(self, *, now: float | None = None) -> list[WatchEvent]:
        current = time.time() if now is None else now
        events: list[WatchEvent] = []
        for path in self._iter_candidates():
            try:
                signature = _signature(path)
            except OSError:
                continue
            if signature == self._processed.get(path):
                continue
            if signature[0] == 0:
                continue  # 空文件通常是刚创建，等下一轮
            try:
                age = current - path.stat().st_mtime
            except OSError:
                continue
            if age < self.config.effective_settle:
                continue
            if not _probe_readable(path):
                continue
            target = self._target_for(path)
            if target == path:
                continue
            rule = OutputRule(
                directory=self.config.target_dir,
                on_conflict=self.config.on_conflict,
                preserve_timestamps=self.config.preserve_timestamps,
            )
            try:
                result = self._converter(path, target, rule)
            except Exception as exc:  # noqa: BLE001 - 单个文件失败不得中断监听
                self._processed[path] = signature
                events.append(WatchEvent(path, "error", error=f"{type(exc).__name__}: {exc}"))
                continue
            self._processed[path] = signature
            output = getattr(result, "output_path", None)
            if output is not None:
                try:
                    self._processed[Path(output)] = _signature(Path(output))
                except OSError:
                    pass
            status = "skipped" if str(getattr(result, "status", "")) == "skipped" else "converted"
            events.append(WatchEvent(
                path,
                status,
                output=Path(output) if output is not None else None,
                warnings=tuple(getattr(result, "warnings", ()) or ()),
            ))
        return events

    def watch(
        self,
        *,
        should_stop: Callable[[], bool] | None = None,
        on_event: Callable[[WatchEvent], None] | None = None,
        max_cycles: int | None = None,
    ) -> list[WatchEvent]:
        collected: list[WatchEvent] = []
        cycles = 0
        while should_stop is None or not should_stop():
            cycles += 1
            for event in self.scan_once():
                collected.append(event)
                if on_event is not None:
                    on_event(event)
            if max_cycles is not None and cycles >= max_cycles:
                break
            if should_stop is not None and should_stop():
                break
            time.sleep(self.config.poll_interval)
        return collected


__all__ = [
    "FolderWatcher",
    "WatchConfig",
    "WatchEvent",
    "default_settle_seconds",
]
