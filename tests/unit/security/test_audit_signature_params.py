"""redact_url_for_audit：预签名 URL 的签名参数必须遮住。

为什么要有这个文件：审计链是 **append-only** —— 一旦把签名写进去就永久留存、
无法撤回。而本仓是**爬虫**，抓到的 URL 里带第三方签名（下载链接、图床 CDN）
是常态。

实测（2026-11-24）：_SENSITIVE_QUERY 只认 signature，于是 ?sig=... 的整段签名
原样进审计链。hmac / bearer / jwt 同理漏掉。

本文件用**矩阵**覆盖：该遮的四类必须遮，不该遮的（nonce / sp / se / sv）
必须保持可见 —— 误拦会损失诊断价值。
"""

from __future__ import annotations

import pytest

from omnicrawler.security.egress import redact_url_for_audit


@pytest.mark.parametrize(
    "param",
    [
        "sig",
        "hmac",
        "bearer",
        "jwt",
        "signature",
        "X-Amz-Signature",
    ],
)
def test_credential_like_params_are_masked(param: str) -> None:
    """这些参数的值是**凭据本身**，必须遮。"""
    out = redact_url_for_audit("https://h/p?" + param + "=SECRETVALUE")
    assert "SECRETVALUE" not in out, param + " 的值仍在审计 URL 里：" + out
    assert "***" in out or "%2A%2A%2A" in out


@pytest.mark.parametrize(
    "param",
    [
        "nonce",
        "sp",
        "se",
        "sv",
        "page",
    ],
)
def test_harmless_params_stay_visible(param: str) -> None:
    """误拦会损失诊断价值 —— 审计要能看出当时请求的是什么。"""
    out = redact_url_for_audit("https://h/p?" + param + "=visible")
    assert "visible" in out, param + " 被误遮：" + out


def test_azure_sas_key_part_is_masked_but_permissions_visible() -> None:
    """真实 Azure SAS 形态：sig 是密钥，sp/se/sv 是元信息。"""
    url = (
        "https://acct.blob.core.windows.net/c/f"
        "?sv=2021-08-06&sp=r&se=2030-01-01&sig=REALSECRET"
    )
    out = redact_url_for_audit(url)
    assert "REALSECRET" not in out, "签名未遮：" + out
    assert "sp=r" in out and "sv=2021-08-06" in out, "元信息被误遮：" + out


def test_no_query_credentials_leaves_the_url_alone() -> None:
    """没有凭据的 URL 不该被改动。"""
    url = "https://h/p?a=1&b=2"
    assert redact_url_for_audit(url) == url


def test_userinfo_is_still_dropped_entirely() -> None:
    """审计链**整个丢弃** userinfo（连用户名也丢）—— 与日志版语义不同。"""
    out = redact_url_for_audit("https://user:pass@host/x")
    assert "user" not in out and "pass" not in out
    assert "host" in out
