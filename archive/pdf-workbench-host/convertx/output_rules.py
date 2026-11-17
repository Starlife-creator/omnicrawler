"""ConvertX 输出策略：命名模板、冲突处理、时间戳与非法字符清洗。

设计对齐成熟格式转换工具的 ``OutputRule`` 概念，但只借用行为语义，不复制源码：

- **命名模板**：``{name}{ext}{date}{time}{index}``；模板不含 ``{ext}`` 时自动补目标后缀，
  避免用户写出无扩展名的文件导致后续格式识别失败。
- **冲突策略**：``overwrite``（保持历史行为）、``rename``、``skip``、``error``。
  源文件在任何策略下都不会被覆盖（``overwrite`` 命中源文件时直接报错）。
- **时间戳**：可选把源文件的 atime/mtime 复制到输出文件。
- **清洗**：Windows/POSIX 非法字符、控制字符、尾随点/空格，以及 Windows 保留名。

本模块不执行 I/O（除时间戳复制），路径解析是纯函数，便于在 GUI/CLI/测试中复用。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

CONFLICT_POLICIES: tuple[str, ...] = ("overwrite", "rename", "skip", "error")
_ALLOWED_TOKENS: frozenset[str] = frozenset({"name", "ext", "date", "time", "index"})
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)
_MAX_NAME_LENGTH = 200


@dataclass(frozen=True, slots=True)
class OutputResolution:
    """输出路径解析结果；``skipped`` 为真时 ``path`` 为 None。"""

    path: Path | None
    skipped: bool = False
    reason: str = ""


def _validate_template(template: str) -> None:
    if not template:
        raise ValueError("ConvertX: 输出命名模板不能为空")
    try:
        parsed = list(re.finditer(r"\{([^{}]+)\}", template))
    except re.error as exc:  # pragma: no cover - 正则固定，仅防御
        raise ValueError(f"ConvertX: 输出命名模板无法解析: {template!r}") from exc
    for match in parsed:
        token = match.group(1)
        if token not in _ALLOWED_TOKENS:
            raise ValueError(
                f"ConvertX: 输出命名模板包含未知占位符 {{{token}}}；"
                f"可用: {', '.join(sorted(_ALLOWED_TOKENS))}"
            )
    # 未闭合的花括号在 format_map 时才报错，这里提前给出可行动信息。
    if "{" not in template and "}" in template:
        raise ValueError(f"ConvertX: 输出命名模板花括号不配对: {template!r}")


def sanitize_filename(name: str) -> str:
    """把任意名称清洗为跨平台可用的单段文件名（不含目录分隔符）。"""
    cleaned = _INVALID_FILENAME_CHARS.sub("_", name).strip().rstrip(". ")
    if not cleaned:
        cleaned = "output"
    if cleaned.split(".")[0].lower() in _WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"
    if len(cleaned) > _MAX_NAME_LENGTH:
        cleaned = cleaned[:_MAX_NAME_LENGTH].rstrip(". ")
    return cleaned


@dataclass(frozen=True, slots=True)
class OutputRule:
    """输出路径策略。

    ``directory`` 为 None 时使用目标路径所在目录。``template`` 生成的是**文件名**，
    目录始终由 ``directory`` 决定（模板里写路径分隔符会被当作非法字符清洗）。
    """

    directory: Path | None = None
    template: str = "{name}{ext}"
    on_conflict: str = "overwrite"
    preserve_timestamps: bool = False
    sanitize: bool = True
    max_rename_attempts: int = 10_000

    def __post_init__(self) -> None:
        _validate_template(self.template)
        if self.on_conflict not in CONFLICT_POLICIES:
            raise ValueError(
                f"ConvertX: 未知冲突策略 {self.on_conflict!r}；可用: {', '.join(CONFLICT_POLICIES)}"
            )
        if self.max_rename_attempts < 1:
            raise ValueError("ConvertX: max_rename_attempts 必须 >= 1")

    @property
    def has_index_token(self) -> bool:
        return "{index}" in self.template


def _render_name(target: Path, rule: OutputRule, *, index: int) -> str:
    stem = target.stem
    extension = target.suffix
    if rule.sanitize:
        stem = sanitize_filename(stem)
    now = datetime.now()
    values = {
        "name": stem,
        "ext": extension,
        "date": now.strftime("%Y%m%d"),
        "time": now.strftime("%H%M%S"),
        "index": str(index),
    }
    rendered = rule.template.format_map(values)
    if "{ext}" not in rule.template:
        rendered += extension
    if rule.sanitize:
        rendered = sanitize_filename(rendered)
    return rendered or f"output{extension}"


def _same_file(left: Path, right: Path) -> bool:
    try:
        return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))
    except OSError:  # pragma: no cover - abspath 在正常路径上不抛
        return False


def _exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return True


def _candidate(target: Path, rule: OutputRule, directory: Path, index: int) -> Path:
    if rule.has_index_token or index == 1:
        return directory / _render_name(target, rule, index=index)
    rendered = Path(_render_name(target, rule, index=1))
    return directory / f"{rendered.stem} ({index}){rendered.suffix}"


def resolve_output_path(
    source: Path,
    target: Path,
    rule: OutputRule | None,
) -> OutputResolution:
    """按策略解析实际输出路径。

    - ``rule is None``：完全保持历史行为（原样返回 target）。
    - ``skip`` 且目标已存在：返回 skipped，不写文件。
    - ``overwrite`` 命中源文件：抛 ``ValueError``（源文件永不覆盖）。
    - ``rename`` 从 1 开始尝试；模板含 ``{index}`` 时用模板编号，否则追加 `` (n)``。
    """
    if rule is None:
        return OutputResolution(target)

    directory = Path(rule.directory) if rule.directory is not None else target.parent
    first = _candidate(target, rule, directory, 1)
    first_is_source = _same_file(first, source)
    first_exists = _exists(first)

    if not first_exists and not first_is_source:
        return OutputResolution(first)

    if first_is_source:
        if rule.on_conflict == "skip":
            return OutputResolution(None, True, "输出路径与源文件相同，按 skip 策略跳过")
        if rule.on_conflict != "rename":
            raise ValueError(f"ConvertX: 输出路径与源文件相同，拒绝覆盖: {first}")
    else:
        if rule.on_conflict == "overwrite":
            return OutputResolution(first)
        if rule.on_conflict == "skip":
            return OutputResolution(None, True, f"目标已存在，按 skip 策略跳过: {first}")
        if rule.on_conflict == "error":
            raise FileExistsError(f"ConvertX: 目标已存在: {first}")

    for index in range(2, rule.max_rename_attempts + 1):
        path = _candidate(target, rule, directory, index)
        if not _exists(path) and not _same_file(path, source):
            return OutputResolution(path)
    raise FileExistsError(
        f"ConvertX: 无法在 {rule.max_rename_attempts} 次内找到可用输出名: {first}"
    )


def apply_timestamps(source: Path, target: Path) -> bool:
    """把源文件 atime/mtime 复制到输出文件；失败返回 False（不中断转换）。"""
    try:
        stat = source.stat()
        os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    except OSError:
        return False
    return True


__all__ = [
    "CONFLICT_POLICIES",
    "OutputResolution",
    "OutputRule",
    "apply_timestamps",
    "resolve_output_path",
    "sanitize_filename",
]
