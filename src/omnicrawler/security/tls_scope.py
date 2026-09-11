"""TLS 校验降级的作用域——把「全局关掉」收紧为「只对显式声明的主机生效」。

## 为什么需要它

`http.verify_tls` 是一个**全局**开关：`src/omnicrawler/fetching/` 下 6 条路径
（requests / httpx / Playwright / websockets / tls_impersonator / browser_pool）都读同一个布尔值。
于是「为了访问一台自签证书的内网机器而关掉校验」会**连带把公网目标也降级**——
用户以为自己只对一台机器放宽，实际是对所有目标放宽。这正是安全项 P2-5 要收紧的点。

## 收紧后的语义

- `verify_tls: true`（默认）：一切照旧，`tls_insecure_domains` 不生效。
- `verify_tls: false`：**必须**用 `tls_insecure_domains` 点名允许免校验的主机；
  且免校验只对名单内的主机生效，**访问名单外的主机会被拦截**。

「名单外直接拦」而不是「名单外仍校验」是刻意的取舍：现有传输栈（httpx / Playwright）
的校验开关是**客户端级**，无法按请求逐主机切换；若只做一半，就会出现「以为只对 A 放宽、
其实对 B 也放宽」的静默不一致。宁可让「混用」明确不可行，也不要留下看不见的降级。

拦截点在 `EgressBroker.authorize`——所有出网的唯一收口，因此 6 条路径一并受约束。
"""

from __future__ import annotations

import ipaddress

#: 配置键名（`http` 段）。
SCOPE_KEY = "tls_insecure_domains"

#: 保留给内网使用的顶级后缀；以它们结尾的主机名视为内网。
INTERNAL_SUFFIXES: tuple[str, ...] = (
    ".local",
    ".internal",
    ".intranet",
    ".lan",
    ".home.arpa",
    ".corp",
)


def invalid_entry_reason(entry: object) -> str | None:
    """返回条目非法原因；``None`` 表示合法。"""
    if not isinstance(entry, str):
        return "必须是字符串"
    value = entry.strip()
    if not value:
        return "不能为空"
    if value != entry:
        return "不能包含首尾空白"
    if "://" in value or "/" in value or ":" in value:
        return "只写主机名，不要带协议、路径或端口"
    if "*" in value:
        return "不支持通配符；请逐台声明主机"
    if " " in value or "\t" in value:
        return "不能包含空白字符"
    return None


def normalized(raw: object) -> tuple[str, ...]:
    """把配置值规范化为小写、去重、保持顺序的主机名元组。"""
    if not isinstance(raw, (list, tuple)):
        return ()
    seen: dict[str, None] = {}
    for item in raw:
        if not isinstance(item, str):
            continue
        host = item.strip().casefold()
        if host:
            seen.setdefault(host, None)
    return tuple(seen)


def _is_private_ip(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
    )


def looks_internal(host: str) -> bool:
    """粗略判断主机是否是内网目标。

    只用于**产生提示**，不用于放行决策——过松或过严都只影响提示语，
    因此不必（也不可能）做到精确。
    """
    if _is_private_ip(host):
        return True
    if "." not in host:
        return True  # 单标签主机名（如 build-server）只在内网可解析
    return host.endswith(INTERNAL_SUFFIXES)


def __domain_matches(host: str, entries: tuple[str, ...]) -> bool:
    host = host.casefold()
    return any(host == entry or host.endswith("." + entry) for entry in entries)


def scope_of(http_section: object) -> tuple[str, ...]:
    """从 `http` 配置段取出规范化的免校验名单。"""
    if not isinstance(http_section, dict):
        return ()
    return normalized(http_section.get(SCOPE_KEY, ()))


def verification_disabled(http_section: object) -> bool:
    """该配置是否关闭了 TLS 校验。"""
    return isinstance(http_section, dict) and http_section.get("verify_tls", True) is False


def allows_unverified(host: str, scope: tuple[str, ...]) -> bool:
    """在已关闭校验的前提下，*host* 是否在显式声明的作用域内。"""
    return __domain_matches(host, scope)
