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


def _parse_env_value(value: str) -> str:
    """剥离引号并反转义；无引号值时按 .env 惯例丢弃行内注释（`` # ...``）。"""
    value = value.strip()
    if value and value[0] in "\"'":
        quote = value[0]
        closing = value.rfind(quote, 1)
        if closing > 0:
            inner = value[1:closing]
            if quote == '"':
                inner = _UNESCAPE_RE.sub(r"\1", inner)  # 双引号内 \" → "、\\ → \
            return inner
        # 未闭合引号：退化为无引号值
    hash_index = value.find(" #")
    if hash_index >= 0:
        value = value[:hash_index].rstrip()
    return value


def parse_env_file(path: Path) -> dict[str, str]:
    """解析 .env 文件为键值映射（忽略注释/空行，剥离成对引号并反转义）。

    S2.1.3 健壮化（源B P2#67）：非 UTF-8 编码不抛裸异常（逐行替换坏字节继续解析）；
    容忍 BOM 前缀、行内注释（`` #``）与 bash 风格 ``export`` 前缀。
    """
    result: dict[str, str] = {}
    if not path.is_file():
        return result
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8", errors="replace")
    for raw_line in text.lstrip("\ufeff").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        raw_key, _, raw_value = stripped.partition("=")
        key = raw_key.strip()
        if key.casefold().startswith("export "):
            key = key[7:].strip()
        if not key:
            continue
        result[key] = _parse_env_value(raw_value)
    return result


def _resolve_env_secret(value: str) -> str:
    """S2.2.2 出口解引用：``secret://<name>`` 值经 secrets_store 还原为明文。

    引用不可解（store 被删/密钥丢失）时保留引用串本身——不含明文，不泄漏；
    调用方以空/缺失语义处理。旧前缀 OMNICRAW_SECRET_* 由 get_secret 兼容。
    """
    stripped = value.strip()
    if not stripped.startswith("secret://"):
        return value
    name = stripped[len("secret://") :]
    try:
        from .credentials import get_secret

        return get_secret(name)
    except Exception:
        return value


def load_ai_env(project_root: str | Path | None = None) -> dict[str, str]:
    """按优先级 os.environ > 项目 .env > 当前目录 .env > 用户级 .env 合并变量。

    返回文件中的全部键值（含非 AI 键，便于调用方按需取用）；
    进程内 os.environ 仅对 AI 相关键做覆盖。
    """
    merged: dict[str, str] = {}
    # S2.1.3：候选列表优先级从高到低（项目 > cwd > 用户级），
    # 因此从低到高遍历，让高层级（项目）最后写入以覆盖低层级（源A P1#81 / 源B P1#18）
    for path in reversed(ai_env_candidates(project_root)):
        merged.update(parse_env_file(path))
    for key in AI_ENV_KEYS:
        if key in os.environ:
            merged[key] = os.environ[key]
    for key, value in merged.items():
        if value is not None:
            merged[key] = _resolve_env_secret(value)
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
            if key.casefold().startswith("export "):
                key = key[7:].strip()
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
