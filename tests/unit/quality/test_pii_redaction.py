"""语料级 PII 检测与脱敏的守卫。

判据不是"能匹配上几个正则"，而是**误报与漏报各自的代价**：

* **误报**会让脱敏后的语料既不可信又丢信息（订单号被当卡号抹掉）⇒ 银行卡走 Luhn、
  身份证走加权校验位，校验不过不报。
* **漏报**会让数据治理结论错、导出包泄露 ⇒ 检测面要覆盖正文中间出现的形态，而
  不是只逐字段整体匹配。
* **报告本身不能成为泄露面** ⇒ 样本必须已脱敏，`pii_report` 里不得出现原值。
"""

from __future__ import annotations

import pytest

from omnicrawler.quality.pii_redaction import (
    detect_pii,
    luhn_valid,
    pii_report,
    redact_pii,
)
from omnicrawler.security.redaction import redact_value

# 真实可校验的样本：Luhn 测试号、身份证加权校验位
_CARD = "4111 1111 1111 1111"
_CARD_DIGITS = "4111111111111111"
_ID = "11010519491231002X"


def _kinds(text: str) -> list[str]:
    return [finding.kind for finding in detect_pii(text)]


# ── 可核验的校验：误报必须被挡住 ──────────────────────────────────────────


def test_luhn_accepts_real_card_and_rejects_order_number() -> None:
    assert luhn_valid(_CARD_DIGITS) is True
    # 19 位订单号：位数够但 Luhn 不过 ⇒ 不得当卡号
    assert luhn_valid("2026092712345678901") is False
    assert luhn_valid("1234") is False  # 位数不够


def test_long_order_number_is_not_redacted() -> None:
    """★ 误报守卫：位数相同但 Luhn 不过的订单号**不得**被抹掉。

    抹掉它＝语料既丢信息又不真实，而语料方会以为"已按合规处理过"。
    """
    text = "订单号 2026092712345678901 已发货"
    assert _kinds(text) == []
    assert redact_pii(text).text == text


def test_bank_card_and_id_card_pass_their_checksums() -> None:
    assert "bank_card" in _kinds(f"卡号 {_CARD} 到期 12/25")
    assert "id_card" in _kinds(f"身份证 {_ID} 已核验")


def test_plain_long_digit_string_is_not_a_secret_token() -> None:
    """纯数字长串交给银行卡/手机号判定，否则 16 位订单号会被当密钥抹掉。"""
    assert "secret_token" not in _kinds("编号 1234567890123456 结束")


# ── 检测面：正文中间出现的形态也要抓到 ────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("联系 alice.smith+tag@example.co.uk 即可", "email"),
        ("手机 13812345678", "phone"),
        (f"身份证 {_ID}", "id_card"),
        (f"卡号 {_CARD}", "bank_card"),
        ("部署在 192.168.1.100", "ipv4"),
        ("token sk9fA2bC4dE6fH8jK0lM2nO4pQ6rS8tU", "secret_token"),
    ],
)
def test_each_kind_is_detected_in_running_text(text: str, kind: str) -> None:
    assert kind in _kinds(text), f"{kind} 未在正文中间被抓到：{text}"


def test_clean_text_yields_nothing() -> None:
    text = "普通商品评论：质量不错，物流快，包装完好。"
    assert _kinds(text) == []
    assert redact_pii(text).text == text


def test_url_credential_is_delegated_to_existing_redactor() -> None:
    """URL 凭据是**结构性**凭据，必须走既有 `redact_url`，不在这里重写一套。"""
    text = "访问 https://user:s3cr3t@internal.example.com/admin"
    result = redact_pii(text)
    assert "s3cr3t" not in result.text
    assert ":pass@" not in result.text
    assert result.counts["url_credential"] == 1


# ── 占位符必须带类型 ───────────────────────────────────────────────────────


def test_placeholder_keeps_the_kind() -> None:
    """只写 ``[REDACTED]`` 会让下游无法区分"原本是邮箱"还是"原本是手机号"，
    而这个区分正是数据治理要用的。"""
    result = redact_pii("邮箱 a@b.com 手机 13812345678")
    assert "[REDACTED:email]" in result.text
    assert "[REDACTED:phone]" in result.text


def test_redaction_removes_the_original_value() -> None:
    for secret in ("alice@example.com", "13812345678", _CARD_DIGITS, "192.168.1.100"):
        assert secret not in redact_pii(f"前缀 {secret} 后缀").text


def test_overlapping_spans_prefer_the_more_specific_kind() -> None:
    """重叠时长的形态赢：更具体的形态优先，避免被宽规则先吃掉。"""
    result = redact_pii(f"卡号 {_CARD}")
    assert result.counts.get("bank_card") == 1
    assert "secret_token" not in result.counts


# ── 报告本身不能成为泄露面 ─────────────────────────────────────────────────


def test_report_never_carries_original_values() -> None:
    """★ 报告常被当作"安全的"随产物外发 ⇒ 样本必须已脱敏。"""
    report = pii_report([f"邮箱 alice@example.com 卡号 {_CARD}", "干净文本"])
    blob = repr(report)
    assert "alice@example.com" not in blob
    assert _CARD_DIGITS not in blob
    assert "干净文本" not in blob, "报告只该带脱敏样本，不该带原文"
    assert "已脱敏" in report["note"]


def test_report_incidence_and_severity() -> None:
    report = pii_report(
        ["邮箱 alice@example.com 手机 13812345678", f"卡号 {_CARD}", "干净文本", ""]
    )
    assert report["documents"] == 4
    assert report["documents_with_pii"] == 2
    assert report["incidence"] == 0.5
    assert report["by_kind"] == {"bank_card": 1, "email": 1, "phone": 1}
    assert report["by_severity"]["high"] == 2  # bank_card + email
    assert report["by_severity"]["medium"] == 1  # phone


def test_report_handles_empty_corpus() -> None:
    report = pii_report([])
    assert report["documents"] == 0
    assert report["incidence"] == 0.0
    assert report["total_findings"] == 0


# ── detect 与 redact 必须分离 ──────────────────────────────────────────────


def test_detect_does_not_modify_text() -> None:
    """检测用于"测量语料里有多少 PII"，改写是另一件事——两者失败代价相反。"""
    text = f"邮箱 alice@example.com 卡号 {_CARD}"
    detect_pii(text)
    assert text == f"邮箱 alice@example.com 卡号 {_CARD}"


def test_redact_on_empty_and_non_string_inputs() -> None:
    assert redact_pii("").text == ""
    assert redact_pii("").total == 0
    assert detect_pii("") == ()
    assert redact_pii(None).text == ""  # type: ignore[arg-type]


# ── 修掉 security.redaction 那个"说谎的 docstring" ────────────────────────


def test_redact_value_does_url_credentials_and_nothing_more() -> None:
    """`redact_value` 的 docstring 曾声称脱敏高熵密钥，函数体里却只有一段注释。

    修法不是让它跨模块委托（那会形成 ``quality ↔ security`` 环、突破
    ``check_architecture`` 预算），而是**让它只声明它真正做到的事**，并把 PII 与
    密钥检测指向 `quality.pii_redaction`。本用例钉住这个边界：
    URL 凭据由它处理，密钥由 PII 模块处理，两者不越界。
    """
    assert "s3cr3t" not in redact_value("https://user:s3cr3t@internal.example.com/x")
    # 高熵密钥**不由**它处理，改由 redact_pii 负责
    secret = "sk9fA2bC4dE6fH8jK0lM2nO4pQ6rS8tU"
    assert redact_value(f"endpoint_token={secret}") == f"endpoint_token={secret}"
    assert secret not in redact_pii(f"endpoint_token={secret}").text

    # ★ 摘要行（help() 与文档工具实际展示的那一行）不得再宣称做密钥脱敏。
    #   整段 docstring 里会提到这段历史，不能整段断言；只有摘要行是"承诺"。
    summary = (redact_value.__doc__ or "").strip().splitlines()[0]
    assert "高熵" not in summary and "base64" not in summary, (
        f"redact_value 的摘要行仍在承诺它做不到的事：{summary}"
    )


def test_redact_value_leaves_ordinary_values_alone() -> None:
    for value in ("hello world", "https://example.com/path", ""):
        assert redact_value(value) == value
