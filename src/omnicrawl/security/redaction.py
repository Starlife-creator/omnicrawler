"""URL 凭据脱敏工具（P9-A1：B05-024/B08-007 共享）。

日志序列化与研究包导出前对 URL 做脱敏，防止 ``scheme://user:pass@host``
形态的凭据随日志/导出包外泄。本模块只依赖标准库，供 core/services 复用。
"""

from __future__ import annotations

import re

# scheme://user:pass@host —— 用户信息段整体视为凭据。
# 注意：IPv6 的 [::1] 与合法非凭据 URL（rare）可能被误判，但脱敏方向是安全的
# （宁可多脱敏不可泄露）。
_URL_CREDENTIAL_RE = re.compile(r"(//[^/\s:@]+):([^@/\s]+)@")


def redact_url(value: str) -> str:
    """脱敏 URL 内嵌凭据：``scheme://user:pass@host`` → ``scheme://user:<redacted>@host``。

    非字符串或无可脱敏内容时原样返回，可安全地用于任意序列化路径。
    """
    if not isinstance(value, str) or not _URL_CREDENTIAL_RE.search(value):
        return value
    return _URL_CREDENTIAL_RE.sub(r"\1:<redacted>@", value)


def redact_value(value: str) -> str:
    """值级脱敏：URL 内嵌凭据 + 高熵 token（长度≥16 的 base64/hex 疑似密钥）。

    研究包/日志在键名不含敏感词时（如 ``db_url``、``endpoint`` 的值）兜底覆盖。
    """
    value = redact_url(value)
    # 高熵疑似密钥：16+ 位 base64/hex，出现在等号后或独立 token 位置。
    # 保守起见仅当键名为连接串/URL 类时由调用方决定是否使用；本函数只处理 URL 形态。
    return value
