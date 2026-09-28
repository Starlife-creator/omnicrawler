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
    """值级脱敏：**仅** URL 内嵌凭据（``scheme://user:pass@host``）。

    研究包/日志在键名不含敏感词时（如 ``db_url``、``endpoint`` 的值）兜底覆盖。

    .. note::
       本函数此前在此处只留了一段关于高熵 token 的**注释**便直接返回，docstring 却
       声称脱敏「长度≥16 的 base64/hex 疑似密钥」——**文档承诺的能力从未实现**。
       现改为只声明它真正做到的事，把 PII 与密钥检测明确指向
       :mod:`omnicrawler.quality.pii_redaction`（那里才有 Luhn / 身份证加权校验
       与语料级画像）。

       刻意**不**在模块顶层 ``import`` 那个模块：本模块是 ``security`` 的最底层，
       引入对 ``quality`` 的依赖会形成 ``quality ↔ security`` 循环依赖并突破
       ``check_architecture`` 的环预算。需要时由调用方自行导入。
    """
    return redact_url(value)
