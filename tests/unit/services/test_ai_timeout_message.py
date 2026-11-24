"""AI 超时消息里的秒数必须消掉浮点噪声。

为什么要有这个文件：`timeout` 来自 `min(self.timeout, self._remaining(deadline))`，
而 `_remaining` 是 `deadline - time.monotonic()` —— 必然产出
`0.30000000000000004` 这类值，原样进错误消息：

```
请求超时（0.30000000000000004s）
请求超时（1e-09s）
```

用户看到十六位小数只会困惑。本文件钉住渲染规则。
"""

from __future__ import annotations

import pytest

from omnicrawler.services.ai_providers import _format_network_error, _human_seconds


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (60.0, "60s"),
        (120.0, "120s"),
        (1.0, "1s"),
        (0.5, "0.50s"),
        (0.30000000000000004, "0.30s"),
        (0.01, "0.01s"),
    ],
)
def test_seconds_are_rendered_cleanly(value: float, expected: str) -> None:
    assert _human_seconds(value) == expected


def test_tiny_values_do_not_render_as_scientific_notation() -> None:
    """`1e-09` 这种科学计数法不得出现在用户面前。"""
    text = _human_seconds(1e-09)
    assert "e-" not in text and "e+" not in text
    assert text == "<0.01s"


def test_long_float_noise_is_removed() -> None:
    """十六位小数要被收敛掉。"""
    text = _human_seconds(0.30000000000000004)
    assert len(text) <= 5, text
    assert text == "0.30s"


def test_whole_seconds_have_no_decimal_point() -> None:
    """`60.0s` 该显示成 `60s` —— 小数点后保留 0 没信息量。"""
    assert "." not in _human_seconds(60.0)


def test_network_timeout_message_uses_the_clean_form() -> None:
    """端到端：消息里不该出现浮点噪声。"""
    message = _format_network_error("openai", TimeoutError(), 0.30000000000000004)
    assert "0.30000000000000004" not in message
    assert "0.30s" in message


def test_network_timeout_message_still_has_guidance() -> None:
    """改格式不能把可执行建议弄丢。"""
    message = _format_network_error("openai", TimeoutError(), 60.0)
    assert "AI 超时设置" in message


def test_non_timeout_errors_are_unchanged() -> None:
    """只改超时分支 —— 其它网络错误的文案不动。"""
    message = _format_network_error("openai", ConnectionError("boom"), 60.0)
    assert "连接失败" in message and "boom" in message
