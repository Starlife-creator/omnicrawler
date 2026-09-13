"""浏览器启动参数的唯一来源：TLS 规则 + 无头保真参数。

背景（2026-09-13 实测，端到端证据见
`tests/integration/browser/test_infinite_scroll_fixed_sample.py`）：

Windows 无头 Chromium 会因原生窗口遮挡计算把渲染挂起（实测 rAF 只跑 1 帧、
IntersectionObserver 与 scroll 事件各 0 次），于是 `window.scrollTo` 到位了、
**靠 rAF / IO / scroll 事件触发的懒加载却永不发生** —— 产品内置的滚动动作对现代站点
形同无效（同一样例：无参数 4 条 / 加保真参数 12 条）。

本文件锁住三件事：① TLS 规则不变（拒绝关闭 + 按 verify_tls 施加）；
② 无头**必然**带上保真参数；③ 有头**不应**被施加；④ 用户已有的 `--disable-features`
必须被**合并**而不是被再追加一个（Chromium 对重复开关只取最后一个值）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.fetching.browser_launch import (
    _HEADLESS_FIDELITY_ARGS,
    _OCCLUSION_FEATURE,
    build_launch_args,
)

_SRC = Path(__file__).resolve().parents[3] / "src" / "omnicrawler" / "fetching"


def _disable_features(args: list[str]) -> list[str]:
    """取出 `--disable-features=` 后面的特性名（用于断言"合并而非重复追加"）。"""
    prefixes = [a[len("--disable-features=") :] for a in args if a.startswith("--disable-features=")]
    assert len(prefixes) <= 1, f"不应出现多个 --disable-features：{args}"
    return prefixes[0].split(",") if prefixes else []


@pytest.mark.parametrize("flag", ["--ignore-certificate-errors", "--ignore-certificate-errors=true"])
def test_user_args_cannot_disable_tls(flag: str) -> None:
    """launch_args 里出现关闭 TLS 的开关必须报错（降级只能走 http.verify_tls）。"""
    with pytest.raises(ValueError, match="禁止关闭 TLS 校验"):
        build_launch_args([flag], headless=True, verify_tls=True)


def test_verify_tls_false_appends_ignore_flag() -> None:
    args = build_launch_args([], headless=False, verify_tls=False)
    assert "--ignore-certificate-errors" in args


def test_verify_tls_true_does_not_add_ignore_flag() -> None:
    args = build_launch_args([], headless=False, verify_tls=True)
    assert "--ignore-certificate-errors" not in args


def test_headless_adds_fidelity_args_and_occlusion_feature() -> None:
    """无头必须带上保真参数，且遮挡特性被合并进 --disable-features。"""
    args = build_launch_args([], headless=True, verify_tls=True)

    for arg in _HEADLESS_FIDELITY_ARGS:
        assert arg in args, f"无头启动缺少保真参数 {arg}：{args}"
    assert _OCCLUSION_FEATURE in _disable_features(args), args


def test_headful_does_not_get_fidelity_args() -> None:
    """有头不受影响（这些开关只为修无头下的渲染挂起）。"""
    args = build_launch_args([], headless=False, verify_tls=True)

    for arg in _HEADLESS_FIDELITY_ARGS:
        assert arg not in args, f"有头启动不应带 {arg}：{args}"
    assert _OCCLUSION_FEATURE not in _disable_features(args), args


def test_existing_disable_features_is_merged_not_replaced() -> None:
    """用户已声明 `--disable-features=Foo` 时必须合并，否则会丢掉他自己的特性。"""
    args = build_launch_args(["--disable-features=Foo"], headless=True, verify_tls=True)

    features = _disable_features(args)
    assert "Foo" in features, f"用户声明的特性被丢掉：{args}"
    assert _OCCLUSION_FEATURE in features, args


def test_occlusion_feature_not_duplicated_when_user_already_set_it() -> None:
    """用户已经关掉遮挡计算时不重复追加。"""
    args = build_launch_args(
        [f"--disable-features={_OCCLUSION_FEATURE}"], headless=True, verify_tls=True
    )
    assert _disable_features(args).count(_OCCLUSION_FEATURE) == 1, args


def test_user_args_preserved_and_deduplicated() -> None:
    args = build_launch_args(
        ["--no-sandbox", "--disable-dev-shm-usage", "--no-sandbox"],
        headless=False,
        verify_tls=True,
    )
    assert args.count("--no-sandbox") == 1, args
    assert "--disable-dev-shm-usage" in args


def test_none_raw_args_is_accepted() -> None:
    """缺省（配置里没有 launch_args）不应报错。"""
    assert build_launch_args(None, headless=False, verify_tls=True) == []


def test_both_browser_paths_use_the_shared_helper() -> None:
    """防再漂移守卫：Playwright 与 Selenium 两条路径都必须走这个助手。

    规则一旦被重新内联回某一条路径，就会出现第二个真源（这正是本次修复前的状态）。
    """
    pool = (_SRC / "browser_pool.py").read_text(encoding="utf-8")
    fetcher = (_SRC / "browser_fetcher.py").read_text(encoding="utf-8")

    for name, source in (("browser_pool.py", pool), ("browser_fetcher.py", fetcher)):
        assert "build_launch_args(" in source, f"{name} 未使用共享助手"
        # 规则本身不应再在调用点出现
        assert "禁止关闭 TLS 校验（--ignore-certificate-errors）" not in source, (
            f"{name} 又重新内联了 TLS 规则文本"
        )
