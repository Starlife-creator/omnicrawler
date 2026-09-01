from __future__ import annotations

import json
import zipfile

import pytest

from omnicrawler.fetching.action_recorder import ActionSequence, ApiCandidate
from omnicrawler.quality.diagnostic_experience import create_redacted_support_bundle, diagnose
from omnicrawler.services.natural_language_task import compile_natural_language
from omnicrawler.services.offline_demo import create_demo_workspace
from omnicrawler.services.ux_service import advanced_rule_summary, draft_quick_task
from omnicrawler.templates.template_application import apply_template


@pytest.mark.parametrize(
    ("intent", "pages", "download", "monitor"),
    [
        ("save_page", 1, False, False),
        ("collect_section", 30, False, False),
        ("download_files", 1, True, False),
        ("monitor_changes", 1, False, True),
    ],
)
def test_quick_tasks_are_conservative_and_explainable(intent, pages, download, monitor):
    draft = draft_quick_task("https://example.com/news", intent)
    assert draft.max_pages == pages
    assert draft.download_files is download
    assert draft.monitor_changes is monitor
    confirmation = draft.confirmation()
    assert confirmation["必须先试跑"] is True
    assert confirmation["为什么这样设置"]
    assert "访问范围" in confirmation["可修改"]


def test_quick_task_rejects_unsafe_or_incomplete_address():
    with pytest.raises(ValueError):
        draft_quick_task("example.com", "save_page")
    with pytest.raises(ValueError):
        draft_quick_task("ftp://example.com/file", "download_files")


def test_natural_language_fallback_never_needs_ai_and_keeps_guards():
    draft = compile_natural_language("每周监测 https://example.com/policy 中“新能源补贴”的 PDF 变化并导出Excel")
    assert draft.task.monitor_changes is True
    assert draft.schedule == "weekly"
    assert draft.topics == ("新能源补贴",)
    assert len(draft.safety_constraints) == 4
    assert all("试跑" in value or value.startswith("不") for value in draft.safety_constraints)


def test_natural_language_can_use_the_first_page_url_when_description_omits_it():
    draft = compile_natural_language("下载公告附件中的 PDF 并导出 Excel", fallback_url="https://example.com/notices")
    assert draft.task.url == "https://example.com/notices"
    assert draft.task.download_files is True


def test_advanced_rules_are_summarised_not_removed():
    passthrough = {"project": {}, "plugins": {"paths": ["x"]}, "session": {"persist_cookies": True}}
    count, names = advanced_rule_summary(passthrough)
    assert count == 2
    assert names == ("plugins", "session")
    assert passthrough["plugins"] == {"paths": ["x"]}


def test_structured_diagnostic_and_redacted_bundle(tmp_path):
    diagnostic = diagnose("HTTP 429 too many requests token=diagnostic-secret", ("自动重试 3 次",))
    assert diagnostic.category == "rate_limit"
    assert diagnostic.recoverable is True
    assert diagnostic.data_impact
    bundle = create_redacted_support_bundle(
        tmp_path / "support.zip", diagnostic,
        ("token=secret-value", "Authorization: Bearer-secret", "normal line"),
        root=tmp_path,
    )
    with zipfile.ZipFile(bundle) as archive:
        log = archive.read("logs-redacted.txt").decode()
        payload = json.loads(archive.read("diagnostic.json"))
    assert "secret-value" not in log
    assert "Bearer-secret" not in log
    assert "diagnostic-secret" not in json.dumps(payload, ensure_ascii=False)
    assert payload["category"] == "rate_limit"


def test_offline_demo_contains_all_primary_routes(tmp_path):
    demo = create_demo_workspace(tmp_path / "demo")
    assert all(path.is_file() for path in (demo.index, demo.api, demo.login, demo.changed, demo.config))
    config = demo.config.read_text(encoding="utf-8")
    assert "kind: file" in config
    assert "xlsx: true" in config


def test_home_exposes_low_barrier_actions(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6", reason="home-page UI test requires optional PyQt6")
    from PySide6.QtWidgets import QApplication, QPushButton

    from omnicrawler.gui.home import HomePage

    app = QApplication.instance() or QApplication([])
    home = HomePage()
    labels = {button.text() for button in home.findChildren(QPushButton)}
    assert {"创建任务", "打开空白任务", "查看全部", "导入任务", "5分钟离线演示"} <= labels
    assert home.task_input.accessibleName()

    drafts = []
    home.quick_task_ready.connect(drafts.append)
    home.task_input.setPlainText("https://example.com/news")
    home._create_task()
    assert drafts and drafts[0].url == "https://example.com/news"

    configs, results = [], []
    home.open_recent_config.connect(configs.append)
    home.open_recent_results.connect(results.append)
    home.set_recent_tasks([{
        "project_name": "news",
        "status": "finished",
        "started_at": "2026-09-01T12:00:00",
        "config_path": "configs/news.yaml",
        "workspace": "work/news",
    }])
    recent_buttons = {button.text(): button for button in home._recent_tasks_host.findChildren(QPushButton)}
    recent_buttons["继续编辑"].click()
    recent_buttons["查看结果"].click()
    assert configs == ["configs/news.yaml"]
    assert results == ["work/news"]
    home.deleteLater()
    app.processEvents()


def test_recorded_actions_can_be_deleted_reordered_and_rerecorded():
    sequence = ActionSequence()
    sequence.add_event({"type": "fill", "selector": "#user", "value": "alice"})
    sequence.add_event({"type": "fill", "selector": "#password", "value": "hidden", "secret": True})
    sequence.add_event({"type": "click", "selector": "#submit"})
    assert sequence.sensitive_steps == (1,)
    sequence.move(2, 0)
    sequence.replace(1, {"type": "fill", "selector": "#user", "value": "bob"})
    removed = sequence.delete(2)
    assert removed.value == "secret://browser_password"
    assert [action.action for action in sequence.actions] == ["click", "fill"]
    assert ApiCandidate("https://example.com/api", sample_status=200, schema_valid=True, within_scope=True).may_suggest_rest
    assert not ApiCandidate("https://other.example/api", sample_status=200, schema_valid=True).may_suggest_rest


def test_template_partial_application_has_business_diff_and_undo():
    current = {"crawl": {"max_pages": 10, "concurrency": 2}, "plugins": {"paths": ["keep"]}}
    template = {"crawl": {"max_pages": 50}, "outputs": {"xlsx": True}, "plugins": {"paths": []}}
    application = apply_template(current, template, ("crawl", "outputs"))
    assert application.after["crawl"] == {"max_pages": 50, "concurrency": 2}
    assert application.after["plugins"] == {"paths": ["keep"]}
    assert {change["business_section"] for change in application.changes} == {"采集范围", "导出结果"}
    assert application.undo() == current


def test_apply_template_restores_safe_http_baseline():
    """B11-006 / B05-009：模板段不得把 http/egress 安全键翻转到宽松方向。"""
    current = {
        "http": {"respect_robots": True, "allow_private_network": False, "verify_tls": True},
        "egress": {"enabled": True},
    }
    malicious_template = {
        "http": {"respect_robots": False, "allow_private_network": True, "verify_tls": False},
        "egress": {"enabled": False},
    }
    application = apply_template(current, malicious_template, ("http", "egress"))
    assert application.after["http"]["respect_robots"] is True
    assert application.after["http"]["allow_private_network"] is False
    assert application.after["http"]["verify_tls"] is True
    assert application.after["egress"]["enabled"] is True


def test_compose_recipe_restores_safe_http_baseline():
    """B11-007：配方不得把 http 安全块翻转到宽松方向。"""
    from omnicrawler.templates.recipe_engine import compose_recipe

    current = {"http": {"respect_robots": True, "verify_tls": True}}
    recipe = {"http": {"respect_robots": False, "verify_tls": False}, "outputs": {"xlsx": True}}
    result = compose_recipe(current, recipe)
    assert result["http"]["respect_robots"] is True
    assert result["http"]["verify_tls"] is True
    assert result["outputs"]["xlsx"] is True


def test_validate_template_rejects_security_overrides(tmp_path):
    """B11-006：validate_template 必须拒绝翻转安全键的模板（fail-closed）。"""
    import yaml

    from omnicrawler.templates.template_catalog import TemplateCatalog
    from omnicrawler.templates.template_health import validate_template

    builtin = tmp_path / "builtin"
    builtin.mkdir()
    raw = {
        "template": {"id": "evil/tpl", "name": "Evil", "category": "generic", "description": "d",
                     "version": "1.0.0", "placeholders": {"seed_url": {"label": "URL", "required": True}}},
        "project": {"name": "evil"},
        "source": {"kind": "static_html", "seeds": ["{{seed_url}}"]},
        "http": {"respect_robots": False, "allow_private_network": True, "verify_tls": False},
    }
    (builtin / "evil.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    record = TemplateCatalog(builtin).discover()[0]
    health = validate_template(record)
    assert health.ok is False
    assert any("respect_robots" in err for err in health.errors)
    assert any("allow_private_network" in err for err in health.errors)
    assert any("verify_tls" in err for err in health.errors)
