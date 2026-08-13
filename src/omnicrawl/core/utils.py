from __future__ import annotations

import copy
import hashlib
import json
import logging
import mimetypes
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def utcnow() -> str:
    # S4.5 P3#128：微秒级精度——同秒多条记录不再无法区分
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def expand_env(value: Any) -> Any:
    """递归展开配置中的 ${VAR} / ${VAR:-default} 占位符（缺失变量替换为空串）。

    需要汇总缺失变量时请使用 :func:`expand_env_checked`。
    """
    expanded, _missing = expand_env_checked(value)
    return expanded


def expand_env_checked(value: Any) -> tuple[Any, list[str]]:
    """与 expand_env 相同，但额外返回缺失（无环境值且无默认）的变量名列表。

    S2.1.2 ③：${VAR} 缺失不再静默——调用方可据此给出 warning 汇总。
    """
    missing: list[str] = []

    def walk(item: Any) -> Any:
        if isinstance(item, str):
            def replace(match: re.Match[str]) -> str:
                name, default = match.group(1), match.group(2)
                if name not in os.environ and default is None and name not in missing:
                    missing.append(name)
                return os.environ.get(name, default or "")
            return _ENV_RE.sub(replace, item)
        if isinstance(item, list):
            return [walk(child) for child in item]
        if isinstance(item, dict):
            return {key: walk(child) for key, child in item.items()}
        return item

    return walk(value), missing


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def canonicalize_url(base_url: str, href: str, *, sort_query: bool = False) -> str | None:
    try:
        joined = urljoin(base_url, href.strip())
        parts = urlsplit(joined)
    except ValueError:
        return None
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return None
    hostname = (parts.hostname or "").lower()
    if not hostname:
        return None
    port = parts.port
    default_port = (parts.scheme.lower() == "http" and port == 80) or (
        parts.scheme.lower() == "https" and port == 443
    )
    display_host = f"[{hostname}]" if ":" in hostname else hostname
    # S4.5 P3#126：保留 userinfo（urlsplit 的 username/password 不再被丢弃）
    userinfo = ""
    if parts.username:
        userinfo = quote(parts.username, safe="")
        if parts.password:
            userinfo += ":" + quote(parts.password, safe="")
        userinfo += "@"
    host = userinfo + display_host if not port or default_port else userinfo + f"{display_host}:{port}"
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True))) if sort_query else parts.query
    return urlunsplit((parts.scheme.lower(), host, parts.path or "/", query, ""))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_filename(url: str, content_type: str = "", disposition: str = "") -> str:
    name = ""
    encoded = re.search(r"filename\*\s*=\s*UTF-8''([^;]+)", disposition, flags=re.I)
    plain = re.search(r'filename\s*=\s*"?([^";]+)', disposition, flags=re.I)
    if encoded:
        from urllib.parse import unquote
        name = unquote(encoded.group(1))
    elif plain:
        name = plain.group(1).strip()
    if not name:
        name = Path(urlsplit(url).path).name or "index"
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", name).strip(" ._") or "file"
    if "." not in name and content_type:
        name += mimetypes.guess_extension(content_type.split(";", 1)[0]) or ""
    digest = hashlib.sha1(url.encode("utf-8"), usedforsecurity=False).hexdigest()[:10]
    path = Path(name)
    return f"{path.stem[:100]}_{digest}{path.suffix[:16].lower()}"


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        # On Windows another process can hold the destination briefly while it
        # reads the previous control/config file.  Retrying the atomic rename
        # preserves all-or-nothing semantics without falling back to an unsafe
        # in-place write.
        for attempt in range(6):
            try:
                os.replace(temp_name, path)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.01 * (2**attempt))
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def excel_safe(value: Any, max_length: int = 32700) -> Any:
    """Excel/CSV 单元格安全化（S4.4 ③：pdfx.safe_cell 统一委托此处）。

    - 截断超长字符串（Excel 单格上限 32767）
    - 公式注入防护：以 = + - @ 开头且非纯数字的字符串前加单引号
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        return value
    value = value[:max_length]
    # S4.5 P3#127：数字含科学计数法（1e10）不被误伤
    if value.lstrip("\t\r\n ").startswith(("=", "+", "-", "@")):
        if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", value):
            return "'" + value
    return value


# ── B-1：合规 User-Agent 分层（自报身份，绝不伪造反指纹） ─────────
# 方法论借鉴：crawler-user-agents / user_agents 项目对「UA 分层」的分类思想，
# 严格合规铁则（任何 profile 必通过，否则 ValueError 拒绝）：
#   1. 必须包含 "OmniCrawler/<version>" 主标识；
#   2. 绝不伪造为真实浏览器（Chrome/Edge/Safari/Firefox 精确版本号 UA 冒充）；
#   3. 绝不以指纹对抗为目的（canvas/webgl/audio/fonts 等指纹相关字样一律禁止出现在
#      profile 名、描述、suffix 中）—— doctor 预检会额外扫描反指纹关键词。

UA_PROFILES: dict[str, dict[str, str]] = {
    # 推荐默认：机器人标识 + 联系信息 留给 suffix（如 +contact: a@b.c），
    # 与 robots.txt +bot 生态兼容，大多数合规站点会识别并给予合理速率限制白名单。
    "polite_bot": {
        "description": "合规自报机器人标识；建议 suffix 补充 +contact: email/url",
        # 故意不附任何平台/渲染引擎，粒度最低；核心标识 OmniCrawler 不变
        # （suffix 会由 build_user_agent 追加）
    },
    # 最小化：仅 OmniCrawler/version，不暴露任何后缀/平台信息；适合 LLM 自主选
    # 「降低指纹可识别度」时用（合规降级方式 = 减小可识别维度，不是伪造）。
    "minimal": {
        "description": "最小指纹粒度，仅 OmniCrawler 主标识",
    },
    # 桌面端：附加「Desktop」与通用兼容 token，便于站点给桌面布局；
    # 绝不伪装 Chrome/XX 精确版本号（token 故意不写版本，只说 compatible desktop）。
    "desktop": {
        "description": "桌面端布局提示（非浏览器伪造）",
        # token：OmniCrawler/{ver} (Desktop; +compatible) suffix
        "prefix_tokens": "(Desktop; +compatible)",
    },
    # 移动端：附加「Mobile」token，便于站点给移动端布局；同样不伪造 Safari。
    "mobile": {
        "description": "移动端布局提示（非浏览器伪造）",
        "prefix_tokens": "(Mobile; +compatible)",
    },
}

UA_DEFAULT_PROFILE = "polite_bot"


def _validate_profile_honest(ua: str, *, profile_name: str) -> None:
    """铁则：生成 UA 后再验证一次，防止后续重构意外滑入浏览器伪造。"""
    from .. import __version__

    must_contain = f"OmniCrawler/{__version__}"
    if must_contain not in ua:
        raise ValueError(
            f"User-Agent profile={profile_name!r} 违反合规铁则："
            f"必须包含诚实自报标识 {must_contain!r}"
        )
    # 绝对禁止：真实浏览器精确版本号冒充的强信号（反指纹伪装核心特征）
    forbidden_patterns = (
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/",
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/",
        "Gecko/20100101 Firefox/",
        "Edg/",
    )
    for sig in forbidden_patterns:
        if sig in ua:
            raise ValueError(
                f"User-Agent profile={profile_name!r} 违反合规铁则："
                f"包含疑似浏览器伪造签名 {sig!r}"
            )


def build_user_agent(profile: str, suffix: str = "") -> str:
    """按 profile 构建合规 User-Agent（所有 profile 必含 OmniCrawler/version）。

    Parameters
    ----------
    profile:
        合法值见 UA_PROFILES.keys()；大小写不敏感；未知键回退 UA_DEFAULT_PROFILE（polite_bot）
        并加一条 warning，不抛错（避免用户 typo 导致抓取直接失败）。
    suffix:
        原 user_agent(suffix=...) 语义不变；典型："+contact: a@b.c"、"+bot"、"PDF LLM extraction"。
    """
    from .. import __version__

    key = (profile or UA_DEFAULT_PROFILE).strip().lower() or UA_DEFAULT_PROFILE
    if key not in UA_PROFILES:
        logging.getLogger(__name__).warning(
            "未知 User-Agent profile %r，回退默认 %r；可用 profile: %s",
            profile, UA_DEFAULT_PROFILE, ", ".join(sorted(UA_PROFILES)),
        )
        key = UA_DEFAULT_PROFILE
    info = UA_PROFILES[key]
    base = f"OmniCrawler/{__version__}"
    prefix_tokens = info.get("prefix_tokens", "")
    if prefix_tokens:
        head = f"{base} {prefix_tokens}"
    else:
        head = base
    if suffix:
        head = f"{head} {suffix}"
    # 铁则：生成后再断言一次，确保代码层面无法意外越界
    _validate_profile_honest(head, profile_name=key)
    return head


def user_agent(suffix: str = "", profile: str | None = None) -> str:
    """Generate a version-tracking User-Agent string with optional compliance profile.

    B-1 新增：按 profile 生成 4 档合规 UA；所有档诚实自报 OmniCrawler/version。

    profile 来源优先级（从高到低）：
        1. 函数显式 *profile* 参数（调用方覆盖，如 intelligent_scraper 强制 polite_bot）
        2. 环境变量 ``OMNICRAWL_UA_PROFILE``（便于 CI/容器统一设置）
        3. 默认 ``polite_bot``（等价于本模块 B-1 之前的「诚实 UA + suffix」行为，零破坏）

    典型 suffix 用法保持不变：``"+contact: change-me@example.com"``、``"+bot"``、
    ``"scoped operation"``、``"Probe"``。
    """
    import os

    if profile is None:
        profile = os.environ.get("OMNICRAWL_UA_PROFILE", UA_DEFAULT_PROFILE)
    return build_user_agent(profile or UA_DEFAULT_PROFILE, suffix=suffix)


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    sensitive = {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key", "api-key"}
    return {
        key: value
        for key, value in headers.items()
        if key.casefold() not in sensitive and not key.casefold().endswith("-api-key")
    }
