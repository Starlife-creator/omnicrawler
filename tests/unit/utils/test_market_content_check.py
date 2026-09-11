"""`tools/check_market_content.py` 的自测——重点是**每类缺陷都能被抓到**。

这个工具的意义是把「审查清单里可机器判定的部分」变成证据，进而给
`plugin_router.classify_in_process_tier` 的 `gates_evidence` 一个真实生产者
（此前该字段**没有任何写入者**，于是「证据齐全 → T1」永远走不到）。
因此自测必须证明：**每一类缺陷都会让它失败**，而不是恒真。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.check_market_content import (
    DEFAULT_MARKET,
    SPDX_ALLOWLIST,
    audit_market,
    derived_review_depth,
    gates_evidence,
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _build_market(
    tmp_path: Path,
    *,
    status: str = "ok",
    plugin_source: str = "def handle(op, payload): return {}\n",
    permissions: list[str] | None = None,
) -> tuple[Path, dict]:
    """构造一个最小但**真实可校验**的市场 checkout。``status`` 用来注入缺陷。"""
    market = tmp_path / "market"
    plugin_dir = market / "plugins" / "demo"
    (plugin_dir / "tests").mkdir(parents=True)

    manifest_text = json.dumps({"package_id": "demo", "version": "1.0.0"})
    files = {
        "plugins/demo/plugin.py": plugin_source,
        "plugins/demo/plugin.py.sig": "signature-bytes",
        "plugins/demo/listing.md": "# demo\n",
        "plugins/demo/creator.identity": "identity\n",
        "plugins/demo/creator.sig": "sig\n",
        "plugins/demo/package.manifest.json": manifest_text,
        "plugins/demo/package.manifest.creator.sig": "sig\n",
        "plugins/demo/package.manifest.maintainer.sig": "sig\n",
        "plugins/demo/README.md": "# demo\n",
    }
    for relative, text in files.items():
        (market / relative).write_text(text, encoding="utf-8")
    (plugin_dir / "tests" / "test_demo.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    entry = {
        "id": "demo",
        "license": "MIT",
        "plugin_file": "plugins/demo/plugin.py",
        "signature_file": "plugins/demo/plugin.py.sig",
        "description_file": "plugins/demo/listing.md",
        "creator_identity_file": "plugins/demo/creator.identity",
        "creator_signature_file": "plugins/demo/creator.sig",
        "package_manifest_file": "plugins/demo/package.manifest.json",
        "creator_package_signature_file": "plugins/demo/package.manifest.creator.sig",
        "maintainer_package_signature_file": "plugins/demo/package.manifest.maintainer.sig",
        "package_manifest_sha256": _sha256(manifest_text),
    }
    if permissions is not None:
        entry["permissions"] = permissions

    if status == "missing_file":
        (market / "plugins/demo/README.md").unlink()
    elif status == "manifest_hash_stale":
        entry["package_manifest_sha256"] = "0" * 64
    elif status == "empty_signature":
        (market / "plugins/demo/plugin.py.sig").write_text("", encoding="utf-8")
    elif status == "bad_license":
        entry["license"] = "GPL-3.0-only"
    elif status == "no_tests":
        for child in (plugin_dir / "tests").iterdir():
            child.unlink()
        (plugin_dir / "tests").rmdir()
    elif status == "declared_reviewed_but_failing":
        entry["review_depth"] = "reviewed"
        (market / "plugins/demo/README.md").unlink()
    elif status == "declared_reviewed_without_evidence":
        entry["review_depth"] = "reviewed"

    (market / "catalog.json").write_text(
        json.dumps({"schema_version": 1, "plugins": [entry]}, ensure_ascii=False), encoding="utf-8"
    )
    return market, entry


# --------------------------------------------------------------------------
# 基线：完全合规的插件必须全通过（否则门禁没有区分度）
# --------------------------------------------------------------------------


def test_compliant_plugin_passes_every_check(tmp_path: Path) -> None:
    market, entry = _build_market(tmp_path)
    evidence = gates_evidence(entry, market)
    assert evidence["passed"] is True, evidence["failed_checks"]
    assert derived_review_depth(evidence) == "reviewed"


def test_allowed_licenses_are_recognised(tmp_path: Path) -> None:
    market, entry = _build_market(tmp_path)
    for license_id in sorted(SPDX_ALLOWLIST):
        entry["license"] = license_id
        assert gates_evidence(entry, market)["checks"]["license_allowlisted"], license_id


# --------------------------------------------------------------------------
# 每类缺陷都必须被抓到
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected_check"),
    [
        ("missing_file", "check:readme_present"),
        ("manifest_hash_stale", "check:package_manifest_sha256"),
        ("empty_signature", "check:signature_nonempty:signature_file"),
        ("bad_license", "check:license_allowlisted"),
        ("no_tests", "check:tests_present"),
    ],
)
def test_each_defect_class_is_detected(tmp_path: Path, status: str, expected_check: str) -> None:
    market, entry = _build_market(tmp_path, status=status)
    evidence = gates_evidence(entry, market)
    assert evidence["passed"] is False, f"{status} 未被检出"
    assert expected_check.removeprefix("check:") in evidence["failed_checks"], evidence["failed_checks"]
    assert derived_review_depth(evidence) == "signed_only"


def test_missing_declared_file_is_detected(tmp_path: Path) -> None:
    market, entry = _build_market(tmp_path, status="missing_file")
    assert gates_evidence(entry, market)["checks"]["readme_present"] is False


# --------------------------------------------------------------------------
# 「声明 vs 证据」一致性（这才是让 review_depth 有据可依的那一步）
# --------------------------------------------------------------------------


def test_declared_reviewed_without_supporting_evidence_is_an_issue(tmp_path: Path) -> None:
    market, _entry = _build_market(tmp_path, status="declared_reviewed_but_failing")
    report = audit_market(market)
    assert report["issues"], "声明 reviewed 但证据不过关，必须报出"
    assert "review_depth" in report["issues"][0]


def test_declared_reviewed_without_gates_evidence_is_an_issue(tmp_path: Path) -> None:
    market, _entry = _build_market(tmp_path, status="declared_reviewed_without_evidence")
    report = audit_market(market)
    assert any("gates_evidence" in issue for issue in report["issues"]), report["issues"]


def test_undeclared_review_depth_is_not_an_issue(tmp_path: Path) -> None:
    """未声明不算违规：本工具提供的正是「要填什么」的输入。"""
    market, _entry = _build_market(tmp_path)
    assert audit_market(market)["issues"] == []


# --------------------------------------------------------------------------
# 离线可用：只对「未声明网络能力」的插件适用，且不得误报 URL 解析
# --------------------------------------------------------------------------


def test_url_parsing_is_not_flagged_as_network(tmp_path: Path) -> None:
    """★ 误报守卫：`urllib.parse` 只是字符串解析，不是网络 I/O。

    首版规则按顶层包 `urllib` 判定，把市场里两个插件误判为「非离线可用」——
    这条用例锁住那个教训。
    """
    source = "from urllib.parse import urlencode\n\ndef handle(op, payload): return {}\n"
    market, entry = _build_market(tmp_path, plugin_source=source)
    evidence = gates_evidence(entry, market)
    assert evidence["checks"]["offline_import_safe"] is True, evidence["failed_checks"]


def test_module_level_network_import_is_flagged(tmp_path: Path) -> None:
    market, entry = _build_market(tmp_path, plugin_source="import socket\n\n\ndef handle(o, p): return {}\n")
    assert gates_evidence(entry, market)["checks"]["offline_import_safe"] is False


def test_network_import_inside_function_is_allowed(tmp_path: Path) -> None:
    """只有**模块级**导入算「导入期触网」；函数内按需导入不算。"""
    source = "def handle(op, payload):\n    import socket\n    return {}\n"
    market, entry = _build_market(tmp_path, plugin_source=source)
    assert gates_evidence(entry, market)["checks"]["offline_import_safe"] is True


def test_network_declaring_plugin_is_exempt_and_flagged_as_needing_network(tmp_path: Path) -> None:
    """声明了网络能力的插件按定义需要网络——该项不适用（不能拿它当「通过」）。"""
    market, entry = _build_market(
        tmp_path,
        plugin_source="import socket\n\n\ndef handle(o, p): return {}\n",
        permissions=["network:scoped"],
    )
    evidence = gates_evidence(entry, market)
    assert evidence["needs_network"] is True
    assert "offline_import_safe" not in evidence["checks"], "不适用项不应进证据（否则是恒真的假信号）"


@pytest.mark.skipif(not (DEFAULT_MARKET / "catalog.json").is_file(), reason="没有市场 checkout")
def test_real_market_has_no_offline_false_positive() -> None:
    """真实市场里没有插件因 `urllib.parse` 之类被误判为「非离线可用」。"""
    report = audit_market(DEFAULT_MARKET)
    for name, item in report["plugins"].items():
        assert "offline_import_safe" not in item["evidence"]["failed_checks"], (
            f"{name} 被判定为导入期触网——先确认不是 URL 解析之类的误报"
        )


def test_missing_catalog_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="找不到 catalog"):
        audit_market(tmp_path / "nope")


# --------------------------------------------------------------------------
# 真实市场必须干净（否则这条门禁一上线就是红的）
# --------------------------------------------------------------------------


@pytest.mark.skipif(not (DEFAULT_MARKET / "catalog.json").is_file(), reason="没有市场 checkout")
def test_real_market_has_no_declaration_mismatches() -> None:
    report = audit_market(DEFAULT_MARKET)
    assert report["issues"] == [], report["issues"]
    assert len(report["plugins"]) >= 1


@pytest.mark.skipif(not (DEFAULT_MARKET / "catalog.json").is_file(), reason="没有市场 checkout")
def test_real_market_evidence_is_differentiated() -> None:
    """证据要有区分度：不能所有插件都通过，也不能全部不通过。"""
    report = audit_market(DEFAULT_MARKET)
    passed = [name for name, item in report["plugins"].items() if item["evidence"]["passed"]]
    assert 0 < len(passed) < len(report["plugins"]), (
        "所有插件的机器证据结果相同，说明检查很可能已失效（恒真或恒假）"
    )
