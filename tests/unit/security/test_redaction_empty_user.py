r"""redact_url：用户名可以为空的连接串（`redis://:pw@host`）。

为什么要有这个文件：`security/redaction.py` 的正则原为 `//[^/\s:@]+:...@
—— **要求用户名非空**。而「只有口令、没有用户名」是标准写法：

```
redis://:pw@cache:6379/0
postgres://:pw@db/x
```

实测（2026-11-24）：这些串在日志里**口令原样保留**（`redis://:pw@cache:6379/0`）。
方向的判断很简单：脱敏工具的漏报 = 凭据外泄，所以宁可多脱不可漏。
"""

from __future__ import annotations

import pytest

from omnicrawler.security.redaction import redact_url, redact_value


@pytest.mark.parametrize(
    "url",
    [
        "redis://:pw@cache:6379/0",
        "postgres://:pw@db:5432/x",
        "amqp://:pw@broker:5672/vhost",
    ],
)
def test_password_only_connection_strings_are_redacted(url: str) -> None:
    """只有口令（用户名为空）必须同样被脱敏。"""
    out = redact_url(url)
    assert "pw" not in out, f"{url} 的口令仍在：{out}"
    assert "<redacted>" in out


def test_password_only_connection_string_keeps_the_host() -> None:
    """脱敏仍要保留「连的是哪台机器」—— 否则日志失去诊断价值。"""
    out = redact_url("redis://:pw@cache:6379/0")
    assert "cache" in out and "6379" in out


def test_normal_userinfo_is_unchanged_in_shape() -> None:
    """原有形态不得因这次放宽而变样（用户名保留、只换口令）。"""
    assert redact_url("https://user:pass@host/x") == "https://user:<redacted>@host/x"
    assert redact_url("socks5://u:p@h:1080") == "socks5://u:<redacted>@h:1080"


def test_urls_without_credentials_are_untouched() -> None:
    """没有凭据的 URL 一律不动 —— 否则日志会变得不可读。"""
    for url in ("https://host/path?q=1", "https://tok123@host/x", "not a url", ""):
        assert redact_url(url) == url


def test_redact_value_delegates_to_redact_url() -> None:
    """`redact_value` 的契约就是转调；空用户名两种入口都要覆盖。"""
    assert "pw" not in redact_value("redis://:pw@cache:6379/0")


def test_non_string_input_is_returned_as_is() -> None:
    """任何序列化路径都可能传进来，不能抛。"""
    assert redact_url(None) is None  # type: ignore[arg-type]
    assert redact_url(123) == 123  # type: ignore[arg-type]
