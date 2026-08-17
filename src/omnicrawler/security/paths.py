"""工作区包含性统一工具（P9-A3）。

所有消费外部可控路径的调用点（解析器 local_dir、数据文件 @file 引用、
重放 raw_path、场景包、模板反馈等）必须经过 require_workspace_path 校验，
防止越界读取工作区外文件。
"""

from __future__ import annotations

from pathlib import Path


def require_workspace_path(
    path: str | Path,
    *,
    root: str | Path,
    what: str = "路径",
) -> Path:
    """解析路径并断言位于工作区内（resolve + relative_to 包含断言）。

    resolved 与 workspace 先各自 resolve 再做 relative_to 判定，
    杜绝 ``../`` 或符号链接绕过。相对路径按 workspace 解析（而非进程 CWD）。
    """
    workspace = Path(root).expanduser().resolve()
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = workspace / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(workspace)
    except ValueError as exc:
        raise ValueError(f"{what}越出工作区，禁止访问: {resolved}") from exc
    return resolved
