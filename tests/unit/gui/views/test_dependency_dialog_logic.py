"""依赖对话框的**项提取**逻辑测试（决策三：原生阻断 / 插件不阻断）。

只测纯函数 ``dependency_install_items``：从预检报告里挑出可安装项并标注
``blocking``。Qt 对话框本身不在此测（需 offscreen，另由 GUI 套件覆盖）。
"""

from __future__ import annotations

from omnicrawler.gui.views.dependency_dialog import (
    dependency_install_items,
    mirror_retry_sources,
    should_offer_mirror_from_payload,
)
from omnicrawler.services.dependency_installer import InstallAttempt, InstallResult


def _report(*checks: dict) -> dict:
    return {"checks": list(checks)}


class TestDependencyInstallItems:
    def test_native_missing_is_blocking(self) -> None:
        report = _report(
            {
                "status": "error",
                "title": "依赖：pdfplumber",
                "message": "未安装；pip install pdfplumber",
                "fix": {
                    "action": "install",
                    "requirement": "pdfplumber",
                    "dependency_class": "native",
                },
            }
        )
        items = dependency_install_items(report)
        assert len(items) == 1
        assert items[0]["requirement"] == "pdfplumber"
        assert items[0]["blocking"] is True

    def test_plugin_missing_is_not_blocking(self) -> None:
        report = _report(
            {
                "status": "warning",
                "title": "插件依赖：academic-paper-downloader",
                "message": "声明的依赖 playwright 未安装",
                "fix": {
                    "action": "install",
                    "requirement": "playwright",
                    "dependency_class": "plugin",
                },
            }
        )
        items = dependency_install_items(report)
        assert len(items) == 1
        assert items[0]["blocking"] is False

    def test_ok_checks_are_ignored(self) -> None:
        report = _report(
            {"status": "ok", "title": "依赖：PyYAML", "message": "已安装", "fix": None},
            {
                "status": "ok",
                "title": "磁盘空间",
                "message": "充足",
                "fix": {"action": "open_folder", "path": "/tmp"},
            },
        )
        assert dependency_install_items(report) == []

    def test_install_without_requirement_skipped(self) -> None:
        # 老式数据（只有 extra 文字提示、没有 requirement）不应被当成可安装项
        report = _report(
            {
                "status": "error",
                "title": "依赖：X",
                "message": "未安装；pip install X",
                "fix": {"action": "install", "extra": "pip install X"},
            }
        )
        assert dependency_install_items(report) == []

    def test_mixed_preserves_both_classes(self) -> None:
        report = _report(
            {
                "status": "error", "title": "原生", "message": "",
                "fix": {"action": "install", "requirement": "pdfplumber", "dependency_class": "native"},
            },
            {
                "status": "warning", "title": "插件", "message": "",
                "fix": {"action": "install", "requirement": "playwright", "dependency_class": "plugin"},
            },
        )
        items = dependency_install_items(report)
        assert {i["requirement"] for i in items} == {"pdfplumber", "playwright"}
        assert sum(1 for i in items if i["blocking"]) == 1

    def test_empty_report(self) -> None:
        assert dependency_install_items({}) == []
        assert dependency_install_items({"checks": []}) == []


class TestInstallResultPayload:
    def test_payload_carries_attempts(self) -> None:
        from omnicrawler.gui.views.dependency_dialog import install_result_payload

        result = InstallResult(
            ok=False,
            package="pdfplumber",
            spec="",
            attempts=[
                InstallAttempt(
                    host="pypi.org", canonical="pypi.org",
                    index_url="https://pypi.org/simple", ok=False, kind="network",
                    detail="连接超时",
                )
            ],
        )
        payload = install_result_payload(result, "pdfplumber")
        assert payload["ok"] is False
        assert payload["requirement"] == "pdfplumber"
        assert payload["attempts"][0]["host"] == "pypi.org"
        assert payload["attempts"][0]["kind"] == "network"


class TestShouldOfferMirrorFromPayload:
    def _payload(self, host: str, kind: str, ok: bool = False) -> dict:
        return {
            "ok": ok,
            "detail": "…",
            "requirement": "playwright",
            "attempts": [{"host": host, "canonical": "pypi.org", "kind": kind, "ok": ok}],
        }

    def test_official_network_failure_offers(self) -> None:
        assert should_offer_mirror_from_payload(
            self._payload("pypi.org", "network"), config_raw={}
        ) is True

    def test_success_does_not_offer(self) -> None:
        assert should_offer_mirror_from_payload(
            self._payload("pypi.org", "network", ok=True), config_raw={}
        ) is False

    def test_already_enabled_does_not_offer(self) -> None:
        assert should_offer_mirror_from_payload(
            self._payload("pypi.org", "network"),
            config_raw={"mirrors": {"enabled": True}},
        ) is False

    def test_version_failure_does_not_offer(self) -> None:
        assert should_offer_mirror_from_payload(
            self._payload("pypi.org", "version"), config_raw={}
        ) is False

    def test_none_config_treated_as_disabled(self) -> None:
        assert should_offer_mirror_from_payload(
            self._payload("pypi.org", "network"), config_raw=None
        ) is True

    def test_empty_payload_does_not_offer(self) -> None:
        assert should_offer_mirror_from_payload({}, config_raw={}) is False


class TestMirrorRetrySources:
    def test_official_first(self) -> None:
        sources = mirror_retry_sources({})
        assert sources[0][1] == "pypi.org"
        assert all(canonical == "pypi.org" for canonical, _host in sources)

    def test_bad_config_falls_back_to_empty(self) -> None:
        # 传入畸形 mirrors 段也不应抛异常；异常时兜底空列表 ⇒ 安装器直连官方
        assert mirror_retry_sources({"mirrors": "不是字典"}) == []
