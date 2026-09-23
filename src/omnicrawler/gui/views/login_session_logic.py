"""「登录会话」页的**纯逻辑**（无 Qt、无网络，可独立单测）—— 《优化方案》§11.1 U3。

把"该显示什么、桥接给谁、降级怎么说"这类可判定的事实从控件里拿出来，好处有两点：

* 判据能在**离屏之外**被钉住（这些是真正会出错的地方：目标站点集合为空、
  剩余时间格式、不可用时的引导文案）；
* 控件层只剩"把字符串放进哪个控件"，GUI 门禁与 i18n 门禁都更容易满足。
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlsplit

from ...core.config import AppConfig
from ...fetching.login_session import LoginPhase
from ...fetching.session_state import SessionSummary
from ..i18n import _

SESSION_SECTION = "session"
BRIDGE_TO_HTTP_KEY = "bridge_to_http"
NOTICE_ACK_KEY = "session.notice_ack_v1"
BRIDGE_OVERRIDE_SETTING = "loginSession.bridgeToHttpOverride"


def bridge_default(config: AppConfig) -> bool:
    """任务配置里的桥接默认值（§11.1 裁定 1：``session.bridge_to_http`` 默认开启）。"""
    return bool(config.section(SESSION_SECTION).get(BRIDGE_TO_HTTP_KEY, True))


def effective_bridge_enabled(config: AppConfig, override: bool | None) -> bool:
    """本次运行的桥接开关：**用户显式切换**优先，未切换时跟随配置默认值。

    ★ 开关只影响**之后的**同步（§11.1 裁定 1）：关掉不删除已存会话，也不改动
    已经归还给 jar 的 cookie。
    """
    if override is None:
        return bridge_default(config)
    return bool(override)


def bridge_hosts(config: AppConfig, *, login_url: str = "") -> tuple[str, ...]:
    """桥接的目标站点集合 = 任务声明的种子主机 + 本次登录地址的主机（去重、保序）。

    ★ 这里的取值直接决定"凭据归还给谁"，因此必须可判定：集合为空时
    ``bridge_storage_state`` 会**显式报错**（不静默全给），而不是悄悄少做事。
    """
    hosts: list[str] = []
    seeds = config.section("source").get("seeds")
    if isinstance(seeds, (list, tuple)):
        for seed in seeds:
            host = urlsplit(str(seed)).hostname
            if host:
                hosts.append(host)
    login_host = urlsplit(login_url).hostname if login_url else None
    if login_host:
        hosts.append(login_host)

    unique: list[str] = []
    seen: set[str] = set()
    for host in hosts:
        # urlsplit().hostname 已经把主机名小写化并去掉尾点，这里只需**精确**去重
        # （再叠加大小写归一属于不可达分支）。
        if host not in seen:
            seen.add(host)
            unique.append(host)
    return tuple(unique)


def format_remaining(seconds: float) -> str:
    """剩余时间 ``m:ss``（已超时/负数一律显示 0:00，不显示负数）。"""
    total = max(0, int(seconds))
    return f"{total // 60}:{total % 60:02d}"


def format_timestamp(epoch: float) -> str:
    """本地时间显示；``0`` 表示取不到（显式占位，不伪装成 1970）。"""
    if epoch <= 0:
        return "-"
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M")


def phase_label(phase: LoginPhase) -> str:
    """状态机阶段 → 用户可读文案（**界面上的状态以此为准**）。"""
    labels = {
        LoginPhase.IDLE: _("未开始"),
        LoginPhase.LAUNCHING: _("正在打开登录窗口…"),
        LoginPhase.WAITING_LOGIN: _("等待你在浏览器窗口中完成登录"),
        LoginPhase.SAVING: _("正在保存登录态…"),
        LoginPhase.SAVED: _("登录态已保存"),
        LoginPhase.FAILED: _("登录会话失败"),
    }
    return labels.get(phase, str(phase))


def degradation_hint(*, playwright_installed: bool, persistence_enabled: bool) -> str:
    """不可用时的引导文案（可行动：缺什么、怎么补）；都可用时返回空串。

    持久化关闭优先提示：这种情况下即使浏览器装好了，登录态也存不下来。
    """
    if not persistence_enabled:
        return _(
            "当前任务未开启 session.persist_cookies，登录态无处保存；"
            "请在任务配置里把它设为 true 后再打开登录窗口。"
        )
    if not playwright_installed:
        return _(
            "未安装 Playwright 浏览器依赖，无法拉起登录窗口；"
            "请安装 browser extra 并执行 python -m playwright install chromium。"
        )
    return ""


def session_row(summary: SessionSummary) -> tuple[str, str, str, str]:
    """会话列表一行 =（账户 / 域名 / cookie 数 / 时间）。

    ★ 解析不出来的快照**照样列出并标注**，不隐藏：悄悄跳过等于让用户以为
    "没有这个会话"。
    """
    if not summary.readable:
        return (
            summary.account,
            _("（快照无法解析）"),
            "-",
            format_timestamp(summary.modified_at),
        )
    domains = "、".join(summary.domains) if summary.domains else "-"
    return (
        summary.account,
        domains,
        str(summary.cookie_count),
        format_timestamp(summary.modified_at),
    )


def notice_text(*, sessions_dir: str, bridge_enabled: bool) -> str:
    """首次打开登录页的一次性提醒正文（§11.1 裁定 1）。

    必须讲清四件事：cookie 属账号凭据 / 落盘位置 / 保护方式 / 关闭入口。
    """
    lines = [
        _("登录窗口只用于你本人手动登录：程序不会代填密码，也不会尝试绕过验证码。"),
        _("登录态的 cookie 属于你的账号凭据，保存在下面这个目录里："),
        sessions_dir,
    ]
    if bridge_enabled:
        lines.append(
            _(
                "按默认设置，登录后会把该站点的 cookie 同步给内置 HTTP 引擎使用"
                "（只归还给目标站点，不含第三方域）。"
            )
        )
    else:
        lines.append(_("当前已关闭同步：登录态只保存在本地，不会同步给 HTTP 引擎。"))
    lines.append(
        _(
            "保护方式与局限：快照按当前用户权限保存（类 Unix 下 0600），"
            "内容为明文 JSON；请勿把工作区目录放到同步盘或共享目录。"
        )
    )
    lines.append(_("你可以随时在本页关闭同步开关，或在这里删除某个已保存的会话。"))
    return "\n".join(lines)
