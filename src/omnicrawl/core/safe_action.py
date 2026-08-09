from __future__ import annotations

import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

_APPLY_FLAGS = {"--apply", "--yes"}


@dataclass
class PlannedAction:
    """描述一个待执行的破坏性动作（dry-run 时输给用户看）。"""

    label: str
    target: str
    detail: str = ""


@dataclass
class SafeActionContext:
    """为一次删除/覆盖操作收集计划与执行能力。"""

    trash_root: Path | None = None
    planned: list[PlannedAction] = field(default_factory=list)

    def plan(self, label: str, target: Path, detail: str = "") -> None:
        self.planned.append(PlannedAction(label=label, target=str(target), detail=detail))


class ConfirmationRequiredError(Exception):
    """缺少显式确认参数时抛出，命令层转为 dry-run 输出。"""


def default_trash_root() -> Path:
    from ..core.runtime_paths import portable_data_root

    return portable_data_root() / "recycle"


_WORKSPACE_MARKERS = {
    "work",
    "data",
    ".runtime",
    "configs",
    "artifacts",
    "output",
    "uploads",
    "workdir",
    ".omnicrawler",
}


def is_workspace_path(path: Path) -> bool:
    """判断目标是否属于项目工作区目录，避免误删任意系统文件。"""
    parts = {part.casefold() for part in path.parts}
    if any(marker in parts for marker in _WORKSPACE_MARKERS):
        return True
    return "omnicrawl" in parts or path.name.casefold() in {"recycle", ".runtime"}


def move_to_recycle(source: Path, *, trash_root: Path | None = None) -> Path:
    """把目标先移入回收站（保留可回滚），返回回收站内新路径。

    - 目标不存在 → 直接返回原路径（幂等）。
    - 非工作区路径 → 拒绝执行并抛 ValueError（安全边界）。
    - 回收站同名冲突自动加时间戳后缀，不覆盖。
    """
    source = source.expanduser().resolve()
    if not source.exists():
        return source
    if not is_workspace_path(source):
        raise ValueError(f"拒绝删除非工作区路径: {source}")

    root = (trash_root or default_trash_root()).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    dest = root / source.name
    if dest.exists():
        stamp = time.strftime("%Y%m%d_%H%M%S")
        dest = root / f"{source.stem}_{stamp}{source.suffix}"
    shutil.move(str(source), str(dest))
    return dest


def require_explicit_apply(action_name: str, argv: list[str] | None = None) -> None:
    """确认破坏性命令带显式应用开关（--apply / --yes）。

    未确认时抛出 :class:`ConfirmationRequiredError`，不执行任何写入。
    """
    args = list(sys.argv[1:]) if argv is None else argv
    if any(flag in args for flag in _APPLY_FLAGS):
        return
    raise ConfirmationRequiredError(
        f"动作 [{action_name}] 是破坏性操作，需要显式 --apply / --yes 才执行"
    )


def require_known_stage(stage: str, known: set[str]) -> None:
    """未知 stage/action 显式报错，替代静默 no-op。"""
    if stage not in known:
        raise ValueError(f"未知阶段/动作: {stage}（可用: {sorted(known)}）")
