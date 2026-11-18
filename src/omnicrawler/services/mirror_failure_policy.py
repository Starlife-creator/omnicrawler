"""市场/组件下载失败的**分类与换源建议**：安全失败绝不提示换源。

## 为什么这条判据是安全关键

用户下载失败时，"要不要建议他换个镜像重试"看似只是体验问题，实际是安全问题：

* **网络类失败**（连不上、DNS 失败、TLS 握手超时）——换镜像确实可能救回来，
  提示是有价值的；
* **安全类失败**（签名校验不过、哈希不符、`sequence` 回退、ABI 不兼容）——
  **换镜像毫无意义，反而危险**：它会把"这包可能被篡改"诱导成"换个源再试一次"，
  用户点下去就等于在绕过校验。

所以本模块把"是否可以建议换源"做成**白名单判定**：只有明确属于网络类的失败
才返回可以，其余一律不可以。默认不提示，而不是默认提示。
"""
from __future__ import annotations

from dataclasses import dataclass

#: 网络类失败：换镜像**有可能**救回来。
NETWORK_FAILURE_KINDS = frozenset(
    {
        "connection",
        "dns",
        "tls_handshake",
        "timeout",
        "http_5xx",
        "reset",
    }
)

#: 安全类失败：换镜像**绝不能**被建议——那等于诱导用户绕过校验。
SECURITY_FAILURE_KINDS = frozenset(
    {
        "signature",
        "hash_mismatch",
        "sequence_rollback",
        "tombstone",
        "abi_incompatible",
        "platform_incompatible",
        "permission",
        "manifest_invalid",
    }
)

#: 其它（配置/参数类）：换源没有帮助，也不涉及安全，保持沉默。
OTHER_FAILURE_KINDS = frozenset({"not_found", "parse", "unknown"})


@dataclass(frozen=True, slots=True)
class FailureVerdict:
    """一次失败的分类结论。"""

    kind: str
    #: 是否可以建议用户换镜像重试。
    may_suggest_mirror: bool
    #: 给用户看的说明（空串表示不需要额外说明）。
    reason: str


def classify_exception(exc: BaseException) -> str:
    """把异常归类成稳定的 kind 字符串。

    **顺序很重要**：安全类必须排在网络类之前判定。`PermissionError` 是
    Python 内置异常，某些网络栈也会抛它，但本仓用它表示"验签失败"——
    所以先认 `PermissionError` 为安全类。
    """
    # 安全类优先：本仓用 PermissionError 表示验签/信任失败
    if isinstance(exc, PermissionError):
        return "permission"

    text = f"{type(exc).__name__}: {exc}".casefold()

    # 安全关键词：即使异常类型是网络类，只要报文里明确是校验失败也按安全处理
    for keyword, kind in (
        ("签名", "signature"),
        ("sha256", "hash_mismatch"),
        ("哈希不符", "hash_mismatch"),
        ("sequence", "sequence_rollback"),
        ("tombstone", "tombstone"),
        ("吊销", "tombstone"),
        ("abi", "abi_incompatible"),
        ("不兼容", "platform_incompatible"),
    ):
        if keyword in text:
            return kind

    # 网络类
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "getaddrinfo" in text or "name or service not known" in text or "dns" in text:
        return "dns"
    if "ssl" in text or "certificate" in text:
        return "tls_handshake"
    if "connection refused" in text or "connection reset" in text or "reset" in text:
        return "reset"

    # 文件不存在 / 解析失败
    if isinstance(exc, FileNotFoundError):
        return "not_found"
    if isinstance(exc, (ValueError, KeyError)):
        return "not_found"

    try:
        import urllib.error

        if isinstance(exc, urllib.error.HTTPError):
            return "http_5xx" if exc.code >= 500 else "not_found"
        if isinstance(exc, (urllib.error.URLError, TimeoutError, OSError)):
            return "connection"
    except ImportError:  # pragma: no cover - urllib 必在标准库
        pass

    return "unknown"


def verdict_for(exc: BaseException) -> FailureVerdict:
    """给出失败分类与"是否可以建议换源"的结论。"""
    kind = classify_exception(exc)

    if kind in SECURITY_FAILURE_KINDS:
        return FailureVerdict(
            kind=kind,
            may_suggest_mirror=False,
            reason=(
                "这是完整性校验失败，换镜像源不会解决问题——"
                "请勿通过切换源绕过校验。"
            ),
        )
    if kind in NETWORK_FAILURE_KINDS:
        return FailureVerdict(
            kind=kind,
            may_suggest_mirror=True,
            reason="当前源连接失败，可以尝试切换镜像源重试。",
        )
    return FailureVerdict(kind=kind, may_suggest_mirror=False, reason="")


def should_suggest_mirror(exc: BaseException) -> bool:
    """便捷判据：是否应当向用户提示"换个镜像试试"。"""
    return verdict_for(exc).may_suggest_mirror


def summarize_failures(
    errors: list[BaseException],
) -> tuple[bool, str]:
    """针对一组失败给出 (是否建议换源, 说明)。

    **只要有一条是安全类失败，就绝不建议换源**——即便同时有网络失败。
    理由：把"可能被篡改"和"连不上"混在一起提示，用户只会记住"换个源再试"。
    """
    if not errors:
        return False, ""

    verdicts = [verdict_for(item) for item in errors]
    if any(item.kind in SECURITY_FAILURE_KINDS for item in verdicts):
        security = next(i for i in verdicts if i.kind in SECURITY_FAILURE_KINDS)
        return False, security.reason
    if any(item.may_suggest_mirror for item in verdicts):
        return True, "部分源连接失败，可以尝试切换镜像源重试。"
    return False, ""
