"""浏览器启动参数的**唯一来源**（Playwright 与 Selenium 两条路径共用）。

两条路径此前各自复制了一份「拒绝 ``launch_args`` 关闭 TLS + 按 ``http.verify_tls`` 施加」
的逻辑（同一规则两个副本），且都没有为无头模式关掉后台/遮挡抑制。本模块把两者收口到一处。

无头保真参数（为什么加）
------------------------

无头 Chromium 会对"被判定为后台或被遮挡"的渲染任务降频。实测（Windows，
同一页面纯空闲 800ms、连续 5 次启动测得的 ``requestAnimationFrame`` 帧数）：

===========================  ==========================
启动方式                      rAF 帧数（连续 5 次）
===========================  ==========================
默认（无参数）                ``[1, 7, 12, 7, 29]``
加上本模块的无头保真参数      ``[13, 43, 14, 38, 11]``
===========================  ==========================

即：帧预算明显更稳、更高（不再出现接近冻结的 1 帧）。这与 Selenium 路径本来就有的
``--disable-background-timer-throttling`` 方向一致；headful 不受影响（只在 headless 时施加）。

**如实声明（避免过度归因）**：我一度以为这些参数能修好"滚动触发懒加载"，
随后用**重复测量否证了自己** —— 同一套参数、同一动作序列，取到的条目数仍在 8~12 之间波动，
而"无参数"也并非必然失败（同样出现 8）。因此本模块**不声称**修好了滚动加载，
它只是把无头页面从"可能被降频"变成"稳定保持在活动状态"。

⇒ 由此得到一条对测试作者的约束：**需要确定性的滚动/懒加载验收时应轮询滚动位置**；
见 `tests/integration/browser/test_infinite_scroll_fixed_sample.py` 的模块 docstring。

**2026-09-16 补充（W3.3 定论）**：后来把"事件驱动"单独量了一遍 —— 结果是
**平台能力没问题**：只要触发元素真的"离开再进入"视口（每次滚动都有真实位移），
IntersectionObserver 与 scroll 事件**各 6/6 达标**；2026-09-13 的"派发不稳定"
**根因是样例一次性滚到底、哨兵不再重新进入**。
但**帧预算仍会出现整轮 0 帧**（800ms rAF 回调实测 `[0, 0, 14, 50, 49]`），
且产品当前的 `scroll_bottom` 在"位置不再变化"时不会产生事件
（同一夹具下事件驱动只拿到 4 / 8 条，位置轮询 12 条稳定）
⇒ **约束不变**：验收用轮询，事件驱动作为真实站点覆盖面另行验收。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

#: 无头模式下关闭后台/遮挡抑制，避免渲染帧预算被压低。
_HEADLESS_FIDELITY_ARGS: tuple[str, ...] = (
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
)

#: 仅在 Windows 存在、会加剧"无头渲染被挂起"的特性名。合并进 ``--disable-features``。
_OCCLUSION_FEATURE = "CalculateNativeWinOcclusion"

_DISABLE_FEATURES_PREFIX = "--disable-features="

_IGNORE_CERT_PREFIX = "--ignore-certificate-errors"

_TLS_REJECT_MESSAGE = (
    "browser.launch_args 禁止关闭 TLS 校验（--ignore-certificate-errors）；"
    "如需关闭请用可审计的 http.verify_tls=false"
)

#: **Playwright 的 channel，必须传给每一次 launch。**
#:
#: 为什么必须有这个常量（条目 5「先做减法」）：
#: Playwright 选择可执行文件的逻辑是
#: ``options.headless ? "chromium-headless-shell" : "chromium"``，
#: 而传 ``channel`` 时会走另一个分支直接返回 ``"chromium"``
#: （``coreBundle.js`` 的 ``getExecutableName``；``chromiumAliases = ["chrome-for-testing"]``）。
#:
#: 我们**不再打包** ``chromium_headless_shell``（269.6 MiB），所以 headless 路径
#: 必须靠这个 channel 指向完整的 Chromium——否则运行期直接
#: ``Executable doesn't exist``。完整 Chromium 是 headless_shell 的超集，
#: 功能不会削弱（代价是 shell 启动更快、内存更省）。
#:
#: 用 ``chrome-for-testing`` 而不是 ``chromium``：前者是 Playwright 明确登记的
#: chromium 别名（``isChromiumAlias`` 只认它），语义更准确。
PLAYWRIGHT_CHROMIUM_CHANNEL = "chrome-for-testing"


@dataclass(frozen=True, slots=True)
class BrowserRuntimeSpec:
    """解析后的浏览器启动规格契约（供 Playwright/Selenium 与登录窗口共享）。"""

    engine_family: str  # "chromium" | "firefox" | "webkit"
    driver_kind: str  # "playwright" | "selenium"
    channel: str | None
    executable_path: str | None
    launch_args: tuple[str, ...]
    verify_tls: bool
    headless: bool = True
    firefox_user_prefs: dict[str, Any] = field(default_factory=dict)


def launch_channel() -> str:
    """返回应传给 ``chromium.launch(channel=...)`` 的值。

    以函数形式提供（而不是让调用点直接引用常量）有两个好处：
    ① 未来要按配置切换 channel 时只改这一处；
    ② 静态守卫能据此识别"这个 launch 已经覆盖了 channel"。
    """
    return PLAYWRIGHT_CHROMIUM_CHANNEL


def resolve_browser_runtime(
    config: Any = None,
    *,
    slot_registry: Any = None,
    preferred_id: str | None = None,
    headless: bool | None = None,
    custom_args: Iterable[object] | None = None,
) -> BrowserRuntimeSpec:
    """由当前配置或槽位首选项解析浏览器启动规范，实现跨内核参数隔离与同源复用。"""
    browser_sec: dict[str, Any] = {}
    http_sec: dict[str, Any] = {}
    if config is not None:
        if hasattr(config, "section"):
            browser_sec = dict(config.section("browser") or {})
            http_sec = dict(config.section("http") or {})
        elif isinstance(config, Mapping):
            browser_sec = dict(config.get("browser") or {})
            http_sec = dict(config.get("http") or {})

    # 若未传 preferred_id 且提供了 slot_registry，优先读取 browser 槽位的活跃偏好
    active_preferred = preferred_id
    if not active_preferred and slot_registry is not None and hasattr(slot_registry, "slot"):
        bslot = slot_registry.slot("browser")
        if bslot is not None and getattr(bslot, "preferred_id", None):
            active_preferred = str(bslot.preferred_id)

    target_id = (
        active_preferred
        or str(browser_sec.get("provider_id") or browser_sec.get("engine") or "playwright")
    ).strip().lower()

    if "firefox" in target_id or "gecko" in target_id:
        engine_family = "firefox"
        driver_kind = "selenium" if "selenium" in target_id else "playwright"
    elif "webkit" in target_id or "safari" in target_id:
        engine_family = "webkit"
        driver_kind = "playwright"
    elif "selenium" in target_id:
        engine_family = "chromium"
        driver_kind = "selenium"
    else:
        engine_family = "chromium"
        driver_kind = "playwright"

    raw_exec = str(browser_sec.get("executable_path") or "").strip()
    executable_path = raw_exec if raw_exec else None

    # channel 仅在 Playwright Chromium 未指定自定义可执行路径时生效
    if driver_kind == "playwright" and engine_family == "chromium" and not executable_path:
        configured_channel = str(browser_sec.get("channel") or "").strip()
        channel = configured_channel if configured_channel else PLAYWRIGHT_CHROMIUM_CHANNEL
    else:
        channel = None

    is_headless = bool(browser_sec.get("headless", True)) if headless is None else bool(headless)
    verify_tls = bool(http_sec.get("verify_tls", True))

    raw_args = list(custom_args) if custom_args is not None else list(browser_sec.get("launch_args") or [])
    for item in raw_args:
        arg = str(item)
        if arg == _IGNORE_CERT_PREFIX or arg.startswith(_IGNORE_CERT_PREFIX + "="):
            raise ValueError(_TLS_REJECT_MESSAGE)

    if engine_family == "chromium":
        final_args = tuple(build_launch_args(raw_args, headless=is_headless, verify_tls=verify_tls))
    else:
        # Firefox 与 WebKit 不接受 Chromium 专属无头保真参数与 occlusion 选项
        cleaned_args: list[str] = [str(a) for a in raw_args]
        final_args = tuple(cleaned_args)

    user_prefs: dict[str, Any] = {}
    if engine_family == "firefox" and not verify_tls:
        user_prefs["webdriver_accept_untrusted_certs"] = True
        user_prefs["acceptInsecureCerts"] = True

    return BrowserRuntimeSpec(
        engine_family=engine_family,
        driver_kind=driver_kind,
        channel=channel,
        executable_path=executable_path,
        launch_args=final_args,
        verify_tls=verify_tls,
        headless=is_headless,
        firefox_user_prefs=user_prefs,
    )


def launch_kwargs(spec: BrowserRuntimeSpec, *, headless: bool | None = None) -> dict[str, Any]:
    """生成传给 playwright.<engine>.launch(**launch_kwargs(spec)) 的参数字典。"""
    is_headless = spec.headless if headless is None else bool(headless)
    kwargs: dict[str, Any] = {
        "headless": is_headless,
        "args": list(spec.launch_args),
    }
    if spec.channel:
        kwargs["channel"] = spec.channel
    if spec.executable_path:
        kwargs["executable_path"] = spec.executable_path
    if spec.firefox_user_prefs:
        kwargs["firefox_user_prefs"] = dict(spec.firefox_user_prefs)
    return kwargs


def launch_playwright_browser(
    playwright: Any,
    spec: BrowserRuntimeSpec,
    *,
    headless: bool | None = None,
) -> Any:
    """按 BrowserRuntimeSpec 拉起对应内核的 Playwright 浏览器实例。"""
    launcher = getattr(playwright, spec.engine_family, playwright.chromium)
    return launcher.launch(**launch_kwargs(spec, headless=headless))


def _merge_disable_features(args: list[str], feature: str) -> None:
    """把 *feature* 合并进已有的 ``--disable-features=``（就地修改）；没有该开关则追加。

    注意**不能**简单再追加一个 ``--disable-features=...``：Chromium 对重复的开关只取
    最后一个值，那样会把用户自己声明的其它特性一起丢掉。
    """
    for index, arg in enumerate(args):
        if arg.startswith(_DISABLE_FEATURES_PREFIX):
            features = [item for item in arg[len(_DISABLE_FEATURES_PREFIX) :].split(",") if item]
            if feature not in features:
                features.append(feature)
                args[index] = _DISABLE_FEATURES_PREFIX + ",".join(features)
            return
    args.append(_DISABLE_FEATURES_PREFIX + feature)


def build_launch_args(
    raw_args: Iterable[object] | None,
    *,
    headless: bool,
    verify_tls: bool,
) -> list[str]:
    """由配置生成最终启动参数列表（Playwright 与 Selenium 共用）。

    :param raw_args: ``browser.launch_args`` 的用户声明。
    :param headless: 是否无头启动 —— 决定是否补保真参数。
    :param verify_tls: ``http.verify_tls``；为假时追加 ``--ignore-certificate-errors``。
    :raises ValueError: 用户声明里试图用 ``--ignore-certificate-errors`` 关闭 TLS 校验
        （把不可审计的降级挡在配置层之外；降级只能走 ``http.verify_tls``）。
    """
    args: list[str] = []
    for item in raw_args or ():
        arg = str(item)
        if arg == _IGNORE_CERT_PREFIX or arg.startswith(_IGNORE_CERT_PREFIX + "="):
            raise ValueError(_TLS_REJECT_MESSAGE)
        if arg not in args:
            args.append(arg)

    if not verify_tls:
        args.append(_IGNORE_CERT_PREFIX)

    if headless:
        for arg in _HEADLESS_FIDELITY_ARGS:
            if arg not in args:
                args.append(arg)
        _merge_disable_features(args, _OCCLUSION_FEATURE)

    return args
