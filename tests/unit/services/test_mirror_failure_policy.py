"""换源提示的安全判据：只有网络类失败才建议换镜像，安全失败必须阻断。

## 为什么这是安全关键

用户下载失败时建议"换个镜像试试"看似是体验优化，实际是安全问题：

| 失败类型 | 换镜像有意义吗 | 若提示换源会怎样 |
|---|---|---|
| 连不上 / DNS / TLS 超时 | 有 | 帮用户绕开网络问题，正面 |
| **签名校验失败** | **没有** | **诱导用户绕过校验**——把"包可能被篡改"变成"换个源再试" |
| **哈希不符** | **没有** | 同上 |
| `sequence` 回退 | 没有 | 用户可能用旧源掩盖新源的吊销信息 |

所以判据必须是**白名单**：明确属于网络类才允许提示，其余默认不提示。

## 补充判据：混合失败时也不提示

只要一组失败里**有一条是安全类**，整体就不建议换源。理由：把"可能被篡改"
和"连不上"混在一起提示，用户只会记住"换个源再试"。
"""

from __future__ import annotations

import urllib.error

import pytest

from omnicrawler.services.mirror_failure_policy import (
    SECURITY_FAILURE_KINDS,
    classify_exception,
    should_suggest_mirror,
    summarize_failures,
    verdict_for,
)


# ── 安全类：绝不允许建议换源 ─────────────────────────────────────────────
@pytest.mark.parametrize(
    "exc",
    [
        PermissionError("catalog.json 签名校验失败（fail-closed）"),
        RuntimeError("构件哈希不符：期望 aa，实际 bb"),
        RuntimeError("sha256 mismatch for wheel"),
        RuntimeError("catalog sequence 回退，拒绝加载"),
        RuntimeError("该版本已吊销（tombstone）"),
        RuntimeError("ABI 不兼容：需要 cp313"),
        RuntimeError("平台不兼容：当前 windows"),
    ],
)
def test_security_failures_never_suggest_mirror(exc: BaseException) -> None:
    """安全类失败绝不能建议换源。"""
    assert should_suggest_mirror(exc) is False, (
        f"{type(exc).__name__}({exc}) 被判成可以换源 —— "
        "这等于诱导用户绕过完整性校验"
    )
    verdict = verdict_for(exc)
    assert verdict.kind in SECURITY_FAILURE_KINDS, (
        f"归类为 {verdict.kind!r}，应属安全类 {sorted(SECURITY_FAILURE_KINDS)}"
    )
    assert verdict.reason, "安全类失败应给出说明，告诉用户换源没用"


# ── 网络类：可以建议换源 ─────────────────────────────────────────────────
@pytest.mark.parametrize(
    "exc",
    [
        TimeoutError("timed out"),
        urllib.error.URLError("Connection refused"),
        urllib.error.URLError("[Errno 11001] getaddrinfo failed"),
        ConnectionResetError("connection reset by peer"),
        RuntimeError("ssl handshake failed"),
    ],
)
def test_network_failures_may_suggest_mirror(exc: BaseException) -> None:
    """网络类失败可以建议换源——这才是提示该出现的场景。"""
    assert should_suggest_mirror(exc) is True, (
        f"{type(exc).__name__}({exc}) 未被判定为网络类，用户得不到有用提示"
    )


# ── 其它类：保持沉默 ─────────────────────────────────────────────────────
def test_not_found_does_not_suggest_mirror() -> None:
    """文件不存在换源没用，不该提示。"""
    assert should_suggest_mirror(FileNotFoundError("no such file")) is False


def test_http_5xx_may_suggest_mirror() -> None:
    """服务端 5xx 属于网络类，换镜像可能救回来。"""
    exc = urllib.error.HTTPError("https://pypi.org/x", 503, "Service Unavailable", {}, None)
    assert should_suggest_mirror(exc) is True


def test_http_4xx_does_not_suggest_mirror() -> None:
    """4xx 是请求/资源问题，换镜像没用。"""
    exc = urllib.error.HTTPError("https://pypi.org/x", 404, "Not Found", {}, None)
    assert should_suggest_mirror(exc) is False


# ── 混合失败：安全类优先 ─────────────────────────────────────────────────
def test_mixed_failures_never_suggest_mirror() -> None:
    """只要有一条安全类失败，整体就不建议换源。

    把"可能被篡改"和"连不上"混在一起提示，用户只会记住"换个源再试"。
    """
    errors: list[BaseException] = [
        TimeoutError("timed out"),  # 网络类
        PermissionError("签名校验失败"),  # 安全类
    ]
    suggest, reason = summarize_failures(errors)
    assert suggest is False, "混合失败时仍建议换源 —— 安全类被网络类掩盖了"
    assert "校验" in reason, f"应说明是校验问题：{reason!r}"


def test_pure_network_failures_may_suggest() -> None:
    """全是网络类时才建议换源。"""
    suggest, reason = summarize_failures([TimeoutError("timed out")])
    assert suggest is True
    assert reason


def test_empty_failures_suggest_nothing() -> None:
    suggest, reason = summarize_failures([])
    assert suggest is False
    assert reason == ""


# ── 分类稳定性 ───────────────────────────────────────────────────────────
def test_classification_is_stable_for_same_exception() -> None:
    """同一异常多次分类结果必须一致（判据要确定性）。"""
    exc = RuntimeError("构件哈希不符")
    kinds = {classify_exception(exc) for _ in range(5)}
    assert len(kinds) == 1, f"分类不稳定：{kinds}"


def test_permission_error_takes_priority_over_network_keywords() -> None:
    """`PermissionError` 即使报文里含网络词，也必须归为安全类。

    本仓用 `PermissionError` 表示验签/信任失败。某些网络栈也会抛它，
    所以判定顺序必须是安全类优先——否则一个验签失败可能被误判成网络问题
    而建议换源。
    """
    exc = PermissionError("connection timeout during signature verify")
    assert classify_exception(exc) == "permission"
    assert should_suggest_mirror(exc) is False


def test_unknown_exception_defaults_to_no_suggestion() -> None:
    """无法归类的异常默认不建议换源（保守）。"""
    class _WeirdError(Exception):
        pass

    assert should_suggest_mirror(_WeirdError("something odd")) is False
