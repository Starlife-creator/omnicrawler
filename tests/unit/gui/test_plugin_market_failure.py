"""安装失败原因链的自测 —— 每类失败都要归到正确的阶段，并给出可行动建议。

P0 的由来：`_on_install_error` 此前只取异常**第一行**塞进会消失的 Toast，
market_client 在明确阶段抛出的有语义异常（验签/下载/目录/解析）全部不可见。
"""

from __future__ import annotations

import json

from omnicrawler.gui.views.plugin_market_logic import (
    install_failure_chain,
    parse_install_failure,
)


def _chain_of(exc: BaseException) -> dict[str, object]:
    return install_failure_chain(exc)


def test_signature_failure_is_classified_as_verify_stage() -> None:
    """验签失败：fail-closed 安全行为，建议必须明确「不要绕过」。"""
    result = _chain_of(PermissionError("插件 demo 签名校验失败（fail-closed 拒载）"))
    assert result["stage"] == "插件验签"
    assert "签名" in str(result["summary"])
    assert "不要" in str(result["advice"]) or "不承诺" in str(result["advice"]) or "fail-closed" in str(result["advice"])


def test_hash_mismatch_is_classified_as_download_check() -> None:
    result = _chain_of(PermissionError("插件 demo 下载校验失败: sha256_mismatch（fail-closed 拒装）"))
    assert result["stage"] == "下载哈希校验"


def test_missing_catalog_entry_is_classified() -> None:
    result = _chain_of(KeyError("catalog 中无此插件: demo"))
    assert result["stage"] == "目录条目"


def test_bad_plugin_id_is_classified() -> None:
    result = _chain_of(ValueError("非法插件 ID: ../evil"))
    assert result["stage"] == "参数校验"


def test_wrapped_network_error_resolves_to_root_cause() -> None:
    """外层 ValueError 包装内层 TimeoutError ⇒ 必须归为**网络**，而不是「解析失败」。"""
    try:
        try:
            raise TimeoutError("connection timed out")
        except TimeoutError as inner:
            raise ValueError("下载失败") from inner
    except ValueError as outer:
        result = _chain_of(outer)
    assert result["stage"] == "网络"
    assert len(result["chain"]) == 2  # ★ 原因链两环都被保留，不是只有第一行


def test_cause_chain_is_fully_preserved_and_copyable() -> None:
    try:
        try:
            try:
                raise PermissionError("签名校验失败")
            except PermissionError as a:
                raise RuntimeError("阶段二失败") from a
        except RuntimeError as b:
            raise ValueError("阶段三失败") from b
    except ValueError as outer:
        result = _chain_of(outer)
    assert len(result["chain"]) == 3
    detail = str(result["detail"])
    assert detail.count(".") == 3 and "RuntimeError" in detail and "PermissionError" in detail


def test_unknown_failure_still_gets_summary_and_detail() -> None:
    """分类不出也要有结论与细节（不能静默空白）。"""
    result = _chain_of(OSError("some odd failure"))
    assert str(result["summary"]).strip()
    assert str(result["detail"]).strip()


def test_parse_accepts_structured_payload() -> None:
    chain = {"stage": "插件验签", "summary": "s", "advice": "a", "detail": "d", "chain": []}
    assert parse_install_failure(json.dumps(chain, ensure_ascii=False)) == chain


def test_parse_rejects_plain_text() -> None:
    """旧格式（纯文本）必须返回 None，由 GUI 走兼容分支，不丢信息。"""
    assert parse_install_failure("普通异常文本") is None
    assert parse_install_failure("") is None
    assert parse_install_failure("{not json") is None
