"""pip 失败归因的枚举值不得原样进用户文案。

为什么要有这个文件：`PipFailureKind` 的三分类是**内部归因**（决定摘流/换源/报用户），
但 `reason_chain()` 把它原样写进给用户看的「逐源原因链」：

```
  - pypi.org (...): 失败[network] boom
```

中文用户读不出 `network` / `version` / `conflict` 的处置差别 ——
而这三类的**下一步动作完全不同**（等网络 / 换源 / 改约束）。

本文件钉住：原因链里必须出现人读标签，且标签说明「该怎么办」。
"""

from __future__ import annotations

import pytest

from omnicrawler.services.dependency_installer import (
    PIP_FAILURE_LABELS,
    InstallAttempt,
    InstallResult,
    PipFailureKind,
)


def _result(kind: str) -> InstallResult:
    attempt = InstallAttempt(
        host="pypi.org",
        canonical="pypi",
        index_url="https://pypi.org/simple",
        ok=False,
        kind=kind,
        detail="boom",
    )
    return InstallResult(
        ok=False,
        package="requests",
        spec="",
        actual_version="",
        attempts=[attempt],
        detail="全部失败",
    )


@pytest.mark.parametrize("kind", [PipFailureKind.NETWORK, PipFailureKind.VERSION, PipFailureKind.CONFLICT])
def test_every_kind_has_a_label(kind: str) -> None:
    """三分类一个都不能漏 —— 漏了就退回英文枚举。"""
    assert kind in PIP_FAILURE_LABELS
    assert PIP_FAILURE_LABELS[kind].strip()


def test_reason_chain_shows_chinese_label_not_enum() -> None:
    """原因链里不该出现英文枚举值。"""
    text = _result(PipFailureKind.NETWORK).reason_chain()
    assert "[network]" not in text, text
    assert "网络故障" in text, text


def test_labels_explain_the_next_action() -> None:
    """三类处置不同，标签要说明该怎么办（否则用户仍不知如何选择）。"""
    assert "重试" in PIP_FAILURE_LABELS[PipFailureKind.NETWORK]
    assert "换源" in PIP_FAILURE_LABELS[PipFailureKind.VERSION]
    assert "约束" in PIP_FAILURE_LABELS[PipFailureKind.CONFLICT]


def test_timeout_retry_is_also_labelled() -> None:
    """大包加时重试的临时归因也会进原因链，同样要有标签。"""
    text = _result("timeout-retry").reason_chain()
    assert "[timeout-retry]" not in text, text
    assert "超时" in text, text


def test_unknown_kind_falls_back_to_the_raw_value() -> None:
    """未知归因不能崩，退回原值（总比抛异常好）。"""
    text = _result("some-future-kind").reason_chain()
    assert "[some-future-kind]" in text, text
