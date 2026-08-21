"""Phase 2a H4 环境诊断报告契约测试（第 71/72 轮）。

锚点：
- report_schema 版本断言（第 71 轮：首行固定携带 report_schema: N，随字段集
  变更单调递增）
- 报告字段白名单（第 69 轮：越界断言，防隐私意外）——零插件明细/零路径/零用户标识
- 报告 schema 版本 ↔ 字段清单一致性（I2 比对思想）
"""

from __future__ import annotations

import pytest

from omnicrawler.plugins import plugin_audit

pytestmark = pytest.mark.plugin_contract


def test_report_schema_version_positive_and_stable() -> None:
    """report_schema 为正整数；当前版本锚点（字段集变更须递增）。"""
    assert plugin_audit.REPORT_SCHEMA_VERSION == 1
    assert isinstance(plugin_audit.REPORT_SCHEMA_VERSION, int)


def test_report_first_line_carries_schema() -> None:
    report = plugin_audit.generate_environment_report()
    assert report.splitlines()[0] == f"```report_schema: {plugin_audit.REPORT_SCHEMA_VERSION}"


def test_report_only_whitelisted_fields() -> None:
    """所有报告字段必须在白名单内（防隐私意外）。"""
    report = plugin_audit.generate_environment_report()
    fields = {
        line.split("|")[1].strip()
        for line in report.splitlines()
        if line.startswith("|") and "---" not in line and "字段" not in line
    }
    out_of_whitelist = fields - plugin_audit.REPORT_FIELD_WHITELIST
    assert out_of_whitelist == set()


def test_report_contains_no_plugin_or_path_data() -> None:
    """零插件明细/零路径/零用户标识（H6 隐私原则的机械断言）。"""
    report = plugin_audit.generate_environment_report()
    lowered = report.lower()
    # 不应出现插件目录路径特征 / 用户目录 / 插件名 example_news
    assert "plugins_installed" not in lowered
    assert "example_news" not in lowered
    assert "/users/" not in lowered and "\\users\\" not in lowered


def test_report_includes_sandbox_probe_fields() -> None:
    """沙箱探测结果入报告（D2/D3 探测项，E_UNSUPPORTED_ENV 回传通道）。"""
    report = plugin_audit.generate_environment_report()
    assert "sandbox_backend" in report
    assert "sandbox_available" in report
    assert "sandbox_supported_range" in report
