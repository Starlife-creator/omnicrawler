"""登录会话快照路径的**唯一真源**（《优化方案》§11.1 U1）。

背景
----

``PlaywrightPool`` 的 ``_state_path`` / ``_context_key`` 是
「``account|proxy`` → ``workspace/sessions/<safe>.playwright.json``」这条规则的
**唯一**实现。登录会话（U1）必须在**同一个位置**读写同一份快照 —— 否则
「登录窗口保存的会话」与「爬取时复用的会话」会指向两个文件，用户手动登录了也白登录。

因此把规则抽到本模块，两端共用：

* 爬取侧：``PlaywrightPool`` 加载 / 保存 ``storage_state``；
* 登录侧：``LoginSessionManager`` 保存用户手动登录的结果。

★ **文件名不含代理凭据**（U1 修正，见 :func:`session_name`）：抽取前的旧规则把
``account|proxy`` 整串替换非法字符后直接当文件名，于是
``http://bob:hunter2@proxy.example:8080`` 的**用户名口令字面留在文件名上** ——
而文件名会出现在目录列表、日志、备份，以及本页必须展示给用户的"落盘位置"里。
新规则把完整身份键只以 sha256 摘要形式落进文件名，代理不再出现。

关于内容安全
------------

``storage_state`` 目前是**明文 JSON + 0600**，这是《优化方案》§11.1 的**既有裁定**
（首期靠 0600 + 路径隔离，AES-GCM 包装列 U 线二期，并在用户指南显式声明）。
本模块只负责**路径**，不读写内容 ⇒ 二期换包装时调用方无感，也不会出现"两处各写一份
明文存储"的分叉。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ..core.config import AppConfig
from ..core.errors import OmniCrawlError
from ..core.models import CrawlRequest

__all__ = [
    "DEFAULT_NAME_FRAGMENT",
    "MAX_ACCOUNT_PREFIX_LENGTH",
    "SESSIONS_DIRNAME",
    "SESSION_NAME_DIGEST_LENGTH",
    "STORAGE_STATE_SUFFIX",
    "SessionPersistenceDisabledError",
    "SessionSummary",
    "context_key",
    "context_key_for_request",
    "list_sessions",
    "remove_session",
    "require_session_state_path",
    "session_name",
    "session_state_path",
    "storage_state_filename",
    "storage_state_path",
    "summarize_session",
]

SESSIONS_DIRNAME = "sessions"
"""workspace 下的会话快照子目录（与 ``CookieSession`` 的 ``.cookies`` 同目录）。"""

STORAGE_STATE_SUFFIX = ".playwright.json"
"""Playwright ``storage_state`` 快照后缀（用以与 ``.cookies`` 区分引擎）。"""

MAX_ACCOUNT_PREFIX_LENGTH = 40
"""文件名里可读账户前缀的最大长度；超出只影响可读性，摘要仍然完整。"""

SESSION_NAME_DIGEST_LENGTH = 12
"""完整身份键的 sha256 摘要长度（十六进制字符数）。"""

DEFAULT_NAME_FRAGMENT = "session"
"""账户名为空/全非法字符时的前缀兜底。"""

_CONTEXT_KEY_SEPARATOR = "|"
_UNSAFE_NAME_FALLBACK = "_"
_ALLOWED_NAME_CHARS = "-_"


class SessionPersistenceDisabledError(OmniCrawlError):
    """``session.persist_cookies`` 关闭时试图保存登录会话。

    ★ 判据纪律：这里必须**显式报错**而不是静默不保存。用户手动登录一次是有
    真实成本的（还要过验证码/短信），静默丢弃会让他下一次采集莫名处于未登录态，
    且现场没有任何线索可查。
    """

    code = "session_persistence_disabled"
    suggestion = (
        "登录态需要落盘才能被后续采集复用，但 session.persist_cookies 当前为 false。"
        "请在任务配置里把它设为 true（GUI 的「登录会话」页也会提示同一件事）。"
    )


def context_key(*, account: str, proxy: str) -> str:
    """构造会话身份键 ``account|proxy`` —— 与 ``PlaywrightPool._context_key`` 同源。

    会话按「账户 + 代理」隔离：同一个账户换代理要重新登录（IP 与会话绑定，
    见《优化方案》§11.6 风险表第 2 行）。
    """
    return f"{account}{_CONTEXT_KEY_SEPARATOR}{proxy}"


def context_key_for_request(config: AppConfig, request: CrawlRequest) -> str:
    """由任务请求推导会话身份键（meta 优先，其次配置）—— 爬取侧原语义。

    原实现内联在 ``PlaywrightPool._context_key``；抽到此处后，登录侧与爬取侧
    共用同一段推导，杜绝"两边默认值不同 ⇒ 指向不同文件"。
    """
    session = config.section("session")
    account = str(request.meta.get("account") or session.get("name", "default"))
    proxy = str(request.meta.get("proxy") or config.section("http").get("proxy", ""))
    return context_key(account=account, proxy=proxy)


def sanitize_name_fragment(value: str) -> str:
    """把字符串转成文件名安全片段。

    只保留 **ASCII** 字母数字与 ``-``/``_``：文件名会出现在日志、备份与 CI 产物里，
    而本仓已有过非 ASCII 路径带来的麻烦（``git ls-files`` 对非 ASCII 路径做八进制转义、
    外部工具按字节读日志）。非 ASCII 账户名只会让**前缀**退化为兜底词，摘要不受影响。
    """
    return "".join(
        char
        if char.isascii() and (char.isalnum() or char in _ALLOWED_NAME_CHARS)
        else _UNSAFE_NAME_FALLBACK
        for char in value
    )


def session_name(context_key: str) -> str:
    """快照文件名主体：``<账户前缀>-<身份摘要>``。

    ★ **代理绝不进文件名**。旧规则（原 ``PlaywrightPool._state_path`` 内联实现）
    把 ``account|proxy`` 整串替换非法字符后直接当文件名 ——
    ``http://bob:hunter2@proxy.example:8080`` 的**用户名口令字面就此留在文件名上**，
    而文件名会出现在目录列表、日志、备份，以及登录会话页必须展示的"落盘位置"里。

    新规则：完整身份键只以 sha256 摘要出现 ⇒ 不同身份必得不同名字（摘要保证唯一性），
    账户前缀仅作人眼可读提示。即使账户本身含 ``|``（异常输入）也只是**前缀**不完整，
    摘要不受影响，会话不会串号。
    """
    account, _, _ = context_key.partition(_CONTEXT_KEY_SEPARATOR)
    prefix = sanitize_name_fragment(account).strip(_UNSAFE_NAME_FALLBACK)
    prefix = prefix[:MAX_ACCOUNT_PREFIX_LENGTH] or DEFAULT_NAME_FRAGMENT
    digest = hashlib.sha256(context_key.encode("utf-8")).hexdigest()[:SESSION_NAME_DIGEST_LENGTH]
    return f"{prefix}-{digest}"


def storage_state_filename(context_key: str) -> str:
    """快照文件名（不含目录）。"""
    return f"{session_name(context_key)}{STORAGE_STATE_SUFFIX}"


def storage_state_path(workspace: Path, value: str) -> Path:
    """快照完整路径。``workspace`` 传 ``config.workspace``。"""
    return Path(workspace) / SESSIONS_DIRNAME / storage_state_filename(value)


def session_state_path(config: AppConfig, value: str) -> Path | None:
    """``persist_cookies`` 关闭时返回 ``None``（与既有 ``_state_path`` 语义一致）。"""
    if not config.section("session").get("persist_cookies", False):
        return None
    return storage_state_path(config.workspace, value)


def require_session_state_path(config: AppConfig, value: str) -> Path:
    """登录会话**必须**有落盘位置 ⇒ 关闭时抛 :class:`SessionPersistenceDisabledError`。"""
    path = session_state_path(config, value)
    if path is None:
        raise SessionPersistenceDisabledError(
            "session.persist_cookies=false：登录会话无处落盘，已拒绝开始登录流程。"
        )
    return path


@dataclass(frozen=True, slots=True)
class SessionSummary:
    """一份会话快照的**元数据摘要**（U3 的会话列表用）。

    ★ 刻意不含任何 cookie 值：列表只需要"哪个账户、哪些域名、多少条、什么时候"。
    """

    name: str
    account: str
    path: Path
    modified_at: float
    cookie_count: int
    domains: tuple[str, ...]
    readable: bool
    """``False`` = 快照存在但解析不出 cookie 列表（损坏/半截写）。

    这种条目**保留在列表里并标出来**，不隐藏 —— 悄悄跳过等于让用户以为
    "没有这个会话"，与"枚举为空要显式"是同一条纪律。
    """

    @property
    def size_bytes(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0


def _account_from_name(name: str) -> str:
    """从文件名主体 ``<账户前缀>-<摘要>`` 反推账户前缀（仅用于显示）。"""
    prefix, separator, _digest = name.rpartition("-")
    if separator and prefix:
        return prefix
    return name


def summarize_session(path: Path) -> SessionSummary:
    """读取一份快照的元数据（**不返回** cookie 值）。"""
    file_path = Path(path)
    stripped = (
        file_path.name[: -len(STORAGE_STATE_SUFFIX)]
        if file_path.name.endswith(STORAGE_STATE_SUFFIX)
        else file_path.name
    )
    try:
        modified_at = file_path.stat().st_mtime
    except OSError:
        modified_at = 0.0

    cookie_count = 0
    domains: tuple[str, ...] = ()
    readable = False
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        data = None
    if isinstance(data, Mapping):
        cookies = data.get("cookies")
        if isinstance(cookies, list):
            readable = True
            cookie_count = len(cookies)
            domains = tuple(
                sorted(
                    {
                        str(item.get("domain", ""))
                        for item in cookies
                        if isinstance(item, Mapping) and item.get("domain")
                    }
                )
            )
    return SessionSummary(
        name=stripped,
        account=_account_from_name(stripped),
        path=file_path,
        modified_at=modified_at,
        cookie_count=cookie_count,
        domains=domains,
        readable=readable,
    )


def list_sessions(workspace: Path) -> tuple[SessionSummary, ...]:
    """列出 ``workspace/sessions`` 下的 storage_state 快照（最近修改的在前）。

    目录不存在 ⇒ 返回空元组（合法的空态，由界面显示空状态而非报错）。
    """
    root = Path(workspace) / SESSIONS_DIRNAME
    if not root.is_dir():
        return ()
    summaries = [
        summarize_session(child)
        for child in sorted(root.glob(f"*{STORAGE_STATE_SUFFIX}"))
        if child.is_file()
    ]
    return tuple(sorted(summaries, key=lambda item: (-item.modified_at, item.name)))


def remove_session(path: Path, *, workspace: Path) -> None:
    """删除一份会话快照。

    ★ 只删 ``<workspace>/sessions/*.playwright.json`` 这一种文件：目标必须落在
    会话目录内、且是该目录的**直接子文件**。不接受任意路径 —— 删除动作的
    合法范围必须由代码约束，而不是由调用方自觉。
    """
    sessions_root = (Path(workspace) / SESSIONS_DIRNAME).resolve()
    target = Path(path).resolve()
    if target.parent != sessions_root or not target.name.endswith(STORAGE_STATE_SUFFIX):
        raise ValueError(f"拒绝删除非会话快照文件：{target.name}")
    target.unlink(missing_ok=True)
