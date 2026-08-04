"""AI 配置环境变量的单一真源读写。

消除历史遗留的三处互不覆盖 .env 拼接（cwd/、~/.omnicrawl/、project_root/）
与多套变量名分裂（OMNICRAWL_AI_* / PDFX_LLM_* / secret://env 解析）。

约定：
- 写入真源：``ai_env_path(project_root)`` —— 项目 .env（无项目时 ~/.omnicrawl/.env）。
- 读取优先级：``os.environ`` > 项目 .env > 当前目录 .env > 用户级 ~/.omnicrawl/.env。
- 唯一变量前缀 ``OMNICRAWL_AI_*``；``PDFX_LLM_*`` 仅作兼容别名，由
  ``bridge_pdfx_llm_env`` 在 pdfx 装载层按需填充（显式配置优先，不覆盖）。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# GUI 与 PDF 子系统统一使用的 AI 配置变量名（唯一真源）
AI_ENV_KEYS: tuple[str, ...] = (
    "OMNICRAWL_AI_PROVIDER",
    "OMNICRAWL_AI_BASE_URL",
    "OMNICRAWL_AI_MODEL",
    "OMNICRAWL_AI_API_KEY",
    "OMNICRAWL_AI_TIMEOUT",
)

# 旧 PDF 模板使用的兼容别名：PDFX_LLM_* → OMNICRAWL_AI_*
PDFX_ALIASES: dict[str, str] = {
    "PDFX_LLM_PROVIDER": "OMNICRAWL_AI_PROVIDER",
    "PDFX_LLM_BASE_URL": "OMNICRAWL_AI_BASE_URL",
    "PDFX_LLM_MODEL": "OMNICRAWL_AI_MODEL",
    "PDFX_LLM_API_KEY": "OMNICRAWL_AI_API_KEY",
    "PDFX_LLM_TIMEOUT": "OMNICRAWL_AI_TIMEOUT",
}

_QUOTE_CHARS = ' #\t"\'$\\'

# 与 _format_env_line 的双引号转义（\" 与 \\）对称的反转义
_UNESCAPE_RE = re.compile(r'\\(["\\])')


def ai_env_path(project_root: str | Path | None = None) -> Path:
    """AI 配置 .env 的单一写入真源。"""
    if project_root:
        return Path(project_root).expanduser() / ".env"
    return Path.home() / ".omnicrawl" / ".env"


def ai_env_candidates(project_root: str | Path | None = None) -> list[Path]:
    """读取候选路径（优先级从高到低）。"""
    paths: list[Path] = []
    if project_root:
        paths.append(Path(project_root).expanduser() / ".env")
    cwd_env = Path.cwd() / ".env"
    if cwd_env not in paths:
        paths.append(cwd_env)
    user_env = Path.home() / ".omnicrawl" / ".env"
    if user_env not in paths:
        paths.append(user_env)
    return paths


def parse_env_file(path: Path) -> dict[str, str]:
    """解析 .env 文件为键值映射（忽略注释/空行，剥离成对引号并反转义）。"""
    result: dict[str, str] = {}
    if not path.is_file():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            quote = value[0]
            value = value[1:-1]
            if quote == '"':
                value = _UNESCAPE_RE.sub(r"\1", value)  # 双引号内 \" → "、\\ → \
        result[key.strip()] = value
    return result


def load_ai_env(project_root: str | Path | None = None) -> dict[str, str]:
    """按优先级 os.environ > 项目 .env > 当前目录 .env > 用户级 .env 合并变量。

    返回文件中的全部键值（含非 AI 键，便于调用方按需取用）；
    进程内 os.environ 仅对 AI 相关键做覆盖。
    """
    merged: dict[str, str] = {}
    for path in ai_env_candidates(project_root):
        merged.update(parse_env_file(path))
    for key in AI_ENV_KEYS:
        if key in os.environ:
            merged[key] = os.environ[key]
    return merged


def _format_env_line(key: str, value: str) -> str:
    """按 .env 约定格式化键值；值含空白/#/引号等特殊字符时加双引号包裹。"""
    value = str(value)
    if value and (value[0] in "\"'" or any(ch in value for ch in _QUOTE_CHARS)):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        value = f'"{escaped}"'
    return f"{key}={value}"


def save_ai_env(
    updates: dict[str, str | None],
    *,
    project_root: str | Path | None = None,
    remove: set[str] | None = None,
) -> Path:
    """行级就地更新 .env：保留注释/空行/顺序，值为 None 或列入 remove 则删除该键。

    返回实际写入的路径。
    """
    path = ai_env_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    remove_keys = set(remove or ())
    update_keys = set(updates) | remove_keys

    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    out: list[str] = []
    rewritten: set[str] = set()
    for line in lines:
        stripped = line.strip()
        key: str | None = None
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.partition("=")[0].strip()
        if key in update_keys:
            value = updates.get(key)
            if value is not None:
                out.append(_format_env_line(key, value))
            rewritten.add(key)
        else:
            out.append(line)
    for key, value in updates.items():
        if key in rewritten:
            continue
        if value is not None:
            out.append(_format_env_line(key, value))
    path.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
    # C40：收紧 .env 文件权限（POSIX 0600；Windows 尽力而为）
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def sync_ai_env_to_os(updates: dict[str, str | None]) -> None:
    """保存后同步进程内 os.environ，保证同会话后续 AI 调用立即可读新值。

    同时使对应的旧 PDFX_LLM_* 桥接/注入值失效，避免同进程内陈旧配置被复用
    （下次 bridge_pdfx_llm_env 会以新 OMNICRAWL_AI_* 重新填充）。
    """
    for key, value in updates.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    for target, source in PDFX_ALIASES.items():
        if source in updates:
            os.environ.pop(target, None)


def bridge_pdfx_llm_env(project_root: str | Path | None = None) -> None:
    """把 OMNICRAWL_AI_* 桥接为 PDFX_LLM_* 兼容别名（填充到 os.environ）。

    供 pdfx 装载层调用，使 CLI/headless 与 GUI 路径一致生效。
    优先级：进程级 PDFX_LLM_*（显式）> .env 中 PDFX_LLM_*（显式，同时填充）> 桥接值。
    """
    ai_vars = load_ai_env(project_root)
    for target, source in PDFX_ALIASES.items():
        if os.environ.get(target) is not None:
            continue  # 进程级显式设置优先（如 GUI PDF 工作台注入）
        if target in ai_vars and ai_vars[target]:
            os.environ[target] = ai_vars[target]  # .env 显式配置优先，并使其对模板展开可见
            continue
        value = ai_vars.get(source)
        if value is not None:
            os.environ[target] = value
