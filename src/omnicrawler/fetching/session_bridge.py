"""storage_state → CookieSession 的**会话桥**（《优化方案》§11.1 U2）。

缺口（《审查记录》§9.2 #6，实测 0 命中）
----------------------------------------

两套会话体系此前**没有桥**：Playwright 侧把登录态存成
``workspace/sessions/<name>.playwright.json``（``storage_state``），HTTP 引擎侧读写
``workspace/sessions/<name>.cookies``（AES-GCM 加密的 LWP jar）；``http_client`` /
``session`` / ``routing`` 里检索 ``storage_state`` / ``playwright.json`` **一次都不命中**。
于是「用浏览器登录了，但用 HTTP 引擎跑任务仍然未登录」。

本模块把那一步补上：把 storage_state 里的 cookie 逐字段转成
:class:`http.cookiejar.Cookie` 并**归还**给目标站点的 jar。

三条判据（§11.1 U2 验收要点）
-----------------------------

1. **逐字段正确**：``HttpOnly`` / ``SameSite`` / ``domain`` 前导点 / ``expires`` /
   ``secure`` / ``path``。字段映射按 CPython ``http.cookiejar`` 的**既有约定**写
   （HttpOnly 与 SameSite 放 ``Cookie._rest``；``domain_initial_dot`` 标前导点；
   Playwright 的 ``expires=-1`` 是"会话 cookie" ⇒ ``expires=None, discard=True``）。
2. **按 domain 归还，绝不整 jar 倒灌**：storage_state 里通常混着第三方/统计域
   cookie，全量倒进 jar 会让**无关域名**也带上本账号的凭据。
3. **枚举为空必须报错**：没有任何 cookie 匹配目标站点（或 ``hosts`` 传空）时**报错**，
   不许静默通过 —— 否则"桥接成功"是假的，用户会以为已经登录。

★ 不做（§11.5 已裁定）：**不注入 localStorage / sessionStorage**。
``storage_state`` 的 ``origins``（localStorage token）**不在本期范围**：给 HTTP 引擎
注入显式 token 是独立的语义变更，触发条件是"出现「storage_state cookie 齐全但仍 401」
的实测任务"。本模块只读 ``cookies``，``origins`` 会被计数并忽略（不静默误当已处理）。

★ 凭据边界：cookie **值**必须离开 storage_state 进入 jar（这是桥的本职），但
**不进日志、不进异常文本、不进返回值** —— :class:`BridgeResult` 只含计数与域名。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from http.cookiejar import Cookie
from pathlib import Path
from typing import Any

from ..core.config import AppConfig
from ..core.errors import OmniCrawlError
from .session import CookieSession, get_cookie_session
from .session_state import SessionPersistenceDisabledError

__all__ = [
    "BridgeResult",
    "SessionBridgeError",
    "bridge_from_storage_state_file",
    "bridge_storage_state",
    "cookie_applies_to_host",
    "playwright_cookie_to_cookie",
    "select_bridgeable_cookies",
]

_SAME_SITE_VALUES = frozenset({"strict", "lax", "none"})
_SESSION_COOKIE_EXPIRES = 0  # Playwright 用 -1 表示无过期时间；<=0 一律当会话 cookie


class SessionBridgeError(OmniCrawlError):
    """会话桥无法完成（storage_state 结构与预期不符、或没有可归还的 cookie）。"""

    code = "session_bridge_error"
    suggestion = (
        "请确认登录窗口里确实登录成功（storage_state 里应有目标站点的 cookie），"
        "并检查任务声明的站点域名是否与登录站点一致。"
    )


@dataclass(frozen=True, slots=True)
class BridgeResult:
    """桥接结果 —— **只含元数据**（计数与域名），不含任何 cookie 值。"""

    added: int
    foreign_skipped: int
    domains: tuple[str, ...]
    origins_ignored: int = 0


def cookie_applies_to_host(cookie_domain: str, host: str) -> bool:
    """cookie 的 ``domain`` 是否覆盖 *host*（RFC 6265 的口径）。

    * 域名 cookie（``.example.org``）：覆盖 ``example.org`` 与其子域；
    * host-only cookie（``example.org``）：**只**覆盖 ``example.org`` 本身。

    ★ 比 jar 自己的返回策略更**严**：stdlib 的 ``domain_return_ok`` 是"自由"匹配
    （共享域判断），这里按标准语义选择，宁少勿多 —— 少归还只是没登录，多归还会把
    账号凭据发给无关站点。
    """
    candidate = (cookie_domain or "").strip().casefold().rstrip(".")
    target = (host or "").strip().casefold().rstrip(".")
    if not candidate or not target:
        return False
    if candidate.startswith("."):
        bare = candidate.lstrip(".")
        return bool(bare) and (target == bare or target.endswith("." + bare))
    return target == candidate


def _expires_to_epoch(raw: Any) -> int | None:
    """Playwright ``expires`` → ``Cookie.expires``；``-1``/缺失 ⇒ ``None``（会话 cookie）。"""
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise SessionBridgeError("cookie 的 expires 字段不是数值，无法安全转换。") from exc
    if value <= _SESSION_COOKIE_EXPIRES:
        return None
    return int(value)


def playwright_cookie_to_cookie(record: Mapping[str, Any]) -> Cookie:
    """Playwright cookie dict → :class:`http.cookiejar.Cookie`（逐字段）。

    ``version=0``：Playwright 的 cookie 是 RFC 6265（Netscape 式）语义。

    ★ 关于 ``HttpOnly`` / ``SameSite``：stdlib 的 ``Cookie`` 没有这两个属性，
    约定放在 ``Cookie._rest`` 里 —— 与 stdlib 自己解析 ``Set-Cookie`` 时
    （无值属性进 rest、值为 ``None``）**同一形态**。写盘再读回时值会变成字符串
    ``"None"``（LWP 格式只存 ``k=v`` 文本），故判据一律看**键是否存在**，不看值。
    """
    name = str(record.get("name") or "")
    if not name:
        raise SessionBridgeError("storage_state 中存在缺少 name 的 cookie 记录。")
    raw_value = record.get("value")
    domain = str(record.get("domain") or "")
    if not domain:
        raise SessionBridgeError(f"cookie {name!r} 缺少 domain，无法判断应归还给谁。")

    same_site = str(record.get("sameSite") or "").strip()
    if same_site and same_site.casefold() not in _SAME_SITE_VALUES:
        # 未知 SameSite 值：不静默丢弃、也不猜 —— 语义不明的字段不许悄悄改写
        raise SessionBridgeError(
            f"cookie {name!r} 的 sameSite 取值不在 Strict/Lax/None 之内：{same_site!r}"
        )

    rest: dict[str, Any] = {}
    if record.get("httpOnly"):
        rest["HttpOnly"] = None
    if same_site:
        rest["SameSite"] = same_site

    raw_path = record.get("path")
    path = str(raw_path) if raw_path else "/"
    domain_initial_dot = domain.startswith(".")
    expires = _expires_to_epoch(record.get("expires"))

    return Cookie(
        version=0,
        name=name,
        value="" if raw_value is None else str(raw_value),
        port=None,
        port_specified=False,
        domain=domain,
        # Playwright 不区分"未写 Domain 属性"与"写了裸域名"，故以有无前导点近似：
        # 前导点 = 显式 Domain 属性（域 cookie），否则视为 host-only。
        domain_specified=domain_initial_dot,
        domain_initial_dot=domain_initial_dot,
        path=path,
        path_specified=bool(raw_path),
        secure=bool(record.get("secure", False)),
        expires=expires,
        discard=expires is None,
        comment=None,
        comment_url=None,
        rest=rest,
    )


def _extract_cookies(state: Mapping[str, Any]) -> Sequence[Any]:
    cookies = state.get("cookies")
    if cookies is None:
        raise SessionBridgeError("storage_state 缺少 cookies 字段，无法桥接。")
    if not isinstance(cookies, (list, tuple)):
        raise SessionBridgeError("storage_state 的 cookies 字段不是列表，无法桥接。")
    return [item for item in cookies if isinstance(item, Mapping)]


def select_bridgeable_cookies(
    cookies: Sequence[Mapping[str, Any]], *, hosts: Iterable[str]
) -> tuple[list[Cookie], int]:
    """按域名挑选**可归还**的 cookie；返回 ``(选中的 Cookie 列表, 被挡在外面的条数)``。

    ``hosts`` 为空 ⇒ 报错（"没有目标站点"时"归还给谁"是未定义的，不能默认全给）。
    """
    targets = [str(host).strip() for host in hosts if str(host).strip()]
    if not targets:
        raise SessionBridgeError("未提供任何目标站点域名：无法确定会话应归还给谁。")
    selected: list[Cookie] = []
    foreign = 0
    for record in cookies:
        domain = str(record.get("domain") or "")
        if any(cookie_applies_to_host(domain, target) for target in targets):
            selected.append(playwright_cookie_to_cookie(record))
        else:
            foreign += 1
    return selected, foreign


def bridge_storage_state(
    state: Mapping[str, Any], session: CookieSession, *, hosts: Iterable[str]
) -> BridgeResult:
    """把 ``storage_state`` 里**属于目标站点**的 cookie 加进 *session* 的 jar（不清空既有）。

    ★ 只增不改：既有 jar 里已有的会话不因桥接而丢失。
    ★ 不落盘：持久化由调用方决定（见 :func:`bridge_from_storage_state_file`），
    这样"进了内存"与"写到磁盘"是两个可分别判定的动作。
    """
    if session.path is None:
        raise SessionPersistenceDisabledError(
            "session.persist_cookies=false：桥接后的 cookie 无处落盘，已拒绝桥接。"
        )
    raw_cookies = _extract_cookies(state)
    selected, foreign = select_bridgeable_cookies(raw_cookies, hosts=hosts)
    if not selected:
        # ★ 判据纪律：假成功比失败更危险 —— 用户会以为"已经登录了"。
        raise SessionBridgeError(
            f"storage_state 里没有属于目标站点的 cookie（共 {len(raw_cookies)} 条，"
            "全部属于其它域名）；桥接未发生。"
        )
    for cookie in selected:
        session.jar.set_cookie(cookie)
    domains = tuple(sorted({cookie.domain for cookie in selected}))
    origins = state.get("origins")
    origins_ignored = len(origins) if isinstance(origins, (list, tuple)) else 0
    return BridgeResult(
        added=len(selected),
        foreign_skipped=foreign,
        domains=domains,
        origins_ignored=origins_ignored,
    )


def bridge_from_storage_state_file(
    config: AppConfig,
    storage_state_path: Path,
    *,
    hosts: Iterable[str],
) -> BridgeResult:
    """从 ``storage_state`` 快照文件桥接到**加密的** ``CookieSession`` 并落盘。

    落盘走既有的 :meth:`CookieSession.save`（AES-GCM + 原子 replace + 0600）
    —— 本模块**不新增任何明文存储**。
    """
    state_path = Path(storage_state_path)
    if not state_path.is_file():
        raise SessionBridgeError(f"storage_state 快照不存在：{state_path.name}")
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SessionBridgeError("storage_state 快照读取失败（文件损坏或不是 JSON）。") from exc
    if not isinstance(data, Mapping):
        raise SessionBridgeError("storage_state 快照的顶层不是对象，无法桥接。")

    session = get_cookie_session(config)
    result = bridge_storage_state(data, session, hosts=hosts)
    session.save()
    return result
