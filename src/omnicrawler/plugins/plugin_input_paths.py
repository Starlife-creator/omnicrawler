"""插件源输入文件的解析与限定 —— 这条判据只在本文件实现一次。

配置校验层（``core/config.py``、``gui/core/validator.py``）只回答「要不要种子 URL」，
不重复这里的边界判定；宿主注入时统一调用本模块。

契约（与 ``docs/PLUGIN_CONTRACT.md`` 的「``source.seed`` 载荷」一节一致）：

- ``source.file`` / ``source.files`` 一律按**运行工作区内**解释：相对路径相对工作区，
  绝对路径也允许，但解析后必须仍落在工作区内；
- 解析**跟随符号链接**，因此指向工作区外的链接同样被拒；
- 越界一律拒绝（``PolicyBlockedError``）——**不做静默回落**。把越界悄悄改写成
  「工作区 + 文件名」会让配置错误看起来像成功，正是本模块要消灭的失败模式；
- 插件 manifest 声明了 ``input_files`` 时，入口必须命中该白名单（``fnmatch`` glob，
  同时按工作区内相对路径与文件名匹配）；未声明则不受白名单约束，只受工作区限定。
"""

from __future__ import annotations

from collections.abc import Iterable
from fnmatch import fnmatch
from pathlib import Path

from ..core.errors import PolicyBlockedError

__all__ = ["resolve_input_entries"]


def _declared_match(rel_posix: str, name: str, patterns: tuple[str, ...]) -> bool:
    """命中白名单：按工作区内相对路径或文件名匹配 glob（manifest 用 ``/`` 分隔）。"""
    return any(fnmatch(rel_posix, pattern) or fnmatch(name, pattern) for pattern in patterns)


def _resolve_one(value: str, root: Path, patterns: tuple[str, ...]) -> str:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    try:
        rel_posix = resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise PolicyBlockedError(
            f"plugin source input file escapes the workspace: {value} -> {resolved} "
            f"(workspace {root})"
        ) from exc
    if patterns and not _declared_match(rel_posix, resolved.name, patterns):
        raise PolicyBlockedError(
            f"plugin source input file is not covered by the plugin's declared input_files: "
            f"{value} -> {rel_posix} (declared {list(patterns)})"
        )
    return str(resolved)


def resolve_input_entries(
    raw_file: object,
    raw_files: object,
    *,
    workspace: object,
    declared: Iterable[str] = (),
) -> tuple[str | None, list[str]]:
    """把 ``source.file`` / ``source.files`` 解析为工作区内的绝对路径。

    Args:
        raw_file: ``source.file``（或 ``source.input_file``）的原始值。
        raw_files: ``source.files`` 的原始值；非数组形态按配置错误处理。
        workspace: 运行工作区根目录。
        declared: 插件 manifest 声明的 ``input_files`` 白名单（glob）。

    Returns:
        ``(file_path, files)``；未声明入口时为 ``(None, [])``。

    Raises:
        PolicyBlockedError: 入口越出工作区、未命中白名单、数组形态非法，或声明了入口
            却拿不到工作区（无法限定 ⇒ 拒绝，而不是不过滤地放行）。
    """
    single = raw_file.strip() if isinstance(raw_file, str) and raw_file.strip() else None
    if raw_files is None:
        multi: list[str] = []
    elif isinstance(raw_files, (list, tuple)):
        multi = [str(item).strip() for item in raw_files if str(item).strip()]
    else:
        raise PolicyBlockedError(f"source.files must be an array, got {type(raw_files).__name__}")
    if single is None and not multi:
        return None, []
    if not isinstance(workspace, (str, Path)) or not str(workspace).strip():
        raise PolicyBlockedError(
            "plugin source declares input files but the run workspace is unknown; "
            "refusing to resolve them without workspace confinement"
        )
    root = Path(str(workspace)).expanduser().resolve()
    patterns = tuple(str(item) for item in declared if str(item).strip())
    resolved_single = _resolve_one(single, root, patterns) if single else None
    return resolved_single, [_resolve_one(item, root, patterns) for item in multi]
