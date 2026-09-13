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

⇒ 由此得到一条对测试作者的约束：**无头下不要依赖 scroll 事件或 IntersectionObserver**
（实测两者派发都不稳定），需要确定性的滚动/懒加载验收时应轮询滚动位置；
见 `tests/integration/browser/test_infinite_scroll_fixed_sample.py` 的模块 docstring。
"""

from __future__ import annotations

from collections.abc import Iterable

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
