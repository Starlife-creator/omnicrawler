"""配置 YAML 解析、重试双轨别名归一、路径包含性与外部路径校验辅助函数。"""

from __future__ import annotations

import difflib
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigParseError

LOGGER = logging.getLogger("omnicrawler")


def parse_yaml_mapping(config_path: Path) -> dict[str, Any]:
    """读取并安全解析 YAML 配置文件为顶层映射，附带行列号诊断。"""
    if not config_path.is_file():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        position = f"，第 {mark.line + 1} 行第 {mark.column + 1} 列" if mark is not None else ""
        problem = getattr(exc, "problem", None) or str(exc)
        LOGGER.error("YAML 语法错误: %s", exc, exc_info=True)
        raise ConfigParseError(f"配置文件 {config_path} 存在语法错误: {problem}{position}") from exc
    if not isinstance(raw, dict):
        raise ValueError("配置文件顶层必须是YAML对象")
    return raw


def resolve_project_root(config_path: Path, merged: dict[str, Any]) -> Path:
    """按显式声明 → pyproject.toml → configs/ 父目录确定性解析项目根。"""
    root = config_path.parent
    declared_root = merged["project"].get("root") if isinstance(merged.get("project"), dict) else None
    if declared_root:
        root = Path(str(declared_root)).expanduser()
        if not root.is_absolute():
            root = (config_path.parent / root).resolve()
        return root
    for candidate in [config_path.parent, *config_path.parents]:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    for candidate in config_path.parents:
        if candidate.name == "configs":
            return candidate.parent
    return root


def _closest_hint(known: list[str], key: str) -> str:
    """给拼写错误配置键一个最接近的合法键候选提示（difflib 相似度 >= 0.6）。"""
    candidates = difflib.get_close_matches(key, known, n=1, cutoff=0.6)
    return f"，是否想写 '{candidates[0]}'？" if candidates else ""


def _apply_retry_alias(merged: dict[str, Any], expanded: dict[str, Any]) -> None:
    """双轨合并（S2.1.4）：旧轨 `http.retry_max` 写进新轨 `http.retries`。

    merged 恒含 DEFAULTS 的 retries，无法区分用户是否显式配置；因此只在用户
    YAML（expanded）中出现 retry_max 时覆盖，retries 仍优先（两者都写时 retries 胜）。
    retries 语义为"总尝试次数"：0 表示不重试。非法 retry_max（负数/非整数）忽略，
    由 validate_config 报错。
    """
    raw_http = expanded.get("http")
    if not isinstance(raw_http, dict):
        return
    if "retries" in raw_http:
        return  # 新轨显式配置优先，retry_max 仅在未写 retries 时兜底
    retry_max = raw_http.get("retry_max")
    if retry_max is None:
        return
    try:
        count = int(retry_max)
    except (TypeError, ValueError):
        return
    if count < 0:
        return
    merged["http"]["retries"] = count


def _check_contained(
    key: str, resolved: Path, root: Path, flag: Callable[[str], None],
) -> None:
    """B05-010：校验绝对路径配置值解析后位于 root 内（真实前缀包含）。"""
    try:
        resolved.relative_to(root)
    except ValueError:
        flag(f"{key} 解析后位于项目根之外: {resolved}（项目根: {root}）")


def require_config_path(path: str | Path, *, require_inside_cwd: bool = False) -> Path:
    """B09-003：消费外部可控 config_path 前校验。

    queue/schedule 的 config_path 来自入队 TaskSpec / 调度库，共享模式下可被
    外部方控制。消费前必须：存在且可读；本地降级模式额外要求位于 CWD 内，
    避免加载越界/攻击者构造的配置。
    """
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"任务配置不存在或不可读: {resolved}")
    if require_inside_cwd:
        cwd = Path.cwd().resolve()
        try:
            resolved.relative_to(cwd)
        except ValueError:
            raise ValueError(f"本地队列模式下 config_path 必须位于当前目录内: {resolved}")
    return resolved

