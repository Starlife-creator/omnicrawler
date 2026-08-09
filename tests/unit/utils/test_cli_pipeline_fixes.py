"""Phase 5b CLI/管线修复（E3-E16）回归测试。"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))


def test_e9_run_task_passes_config_path_to_application_service(monkeypatch, tmp_path) -> None:
    """E9：run_task 统一走 ApplicationService，传入正确配置路径且透传 max_pages/callback。"""
    from omnicrawl.commands import run_task

    cfg = tmp_path / "p.yaml"
    cfg.write_text(
        "project:\n  name: p\n  workspace: work/p\n"
        "source:\n  kind: static_html\n  seeds: [https://example.com]\n"
        "outputs:\n  jsonl: true\n  csv: true\n  xlsx: true\n",
        encoding="utf-8",
    )
    captured: dict = {}

    class FakeService:
        def __init__(self, path):
            captured["path"] = str(path)

        def run(self, **kwargs):
            captured["kwargs"] = kwargs
            return {"status": "succeeded"}

    monkeypatch.setattr(run_task, "ApplicationService", FakeService)
    result = run_task.execute(str(cfg), "run", max_pages=7)
    assert captured["path"] == str(cfg.resolve())
    assert captured["kwargs"]["max_pages"] == 7
    assert captured["kwargs"]["callback"] is None
    assert result["status"] == "succeeded"


def test_e3_status_execute_carries_config_path(tmp_path) -> None:
    """E3：omnicrawl status 的 result 携带 config_path，不再永远打空。"""
    from omnicrawl.commands.run_status import execute

    cfg = tmp_path / "project.yaml"
    cfg.write_text(
        "project:\n  name: p\n  workspace: work/p\n"
        "source:\n  kind: static_html\n  seeds: [https://example.com]\n"
        "outputs:\n  jsonl: true\n  csv: true\n  xlsx: true\n",
        encoding="utf-8",
    )
    result = execute(str(cfg))
    assert result["config_path"] == str(cfg.resolve())


def test_e4_init_project_root_points_at_repo_root(tmp_path) -> None:
    """E4：init-project 的 examples 回退路径基于仓库根而非 src/。"""
    from omnicrawl.commands.init_project import execute

    # examples/configs/browser.yaml 在仓库根；若 parents[2]（src/）则找不到并抛 FileNotFoundError
    result = execute("browser", str(tmp_path), "demo")
    assert result["created"].endswith("demo.yaml")


def test_e13_render_runs_validation(tmp_path) -> None:
    """E13：template render 后跑校验（合法渲染成功且返回校验提示）。"""
    from omnicrawl.commands.template import execute

    result = execute(
        "render", template_id="browser", sets=[],
        output=str(tmp_path / "out.yaml"), force=False,
    )
    assert result["created"].endswith("out.yaml")
    assert "validate" in result["next"]


def test_e14_rendered_value_placeholder_not_misreported(tmp_path) -> None:
    """E14：替换值本身含占位符不被误报为缺失键（missing 只记真正缺失的键）。"""
    import yaml

    from omnicrawl.templates.template_catalog import TemplateCatalog

    # 用户模板：body 引用 {{title}}，我们把它替换成含 {{other}} 的值
    user_dir = tmp_path / "user_templates"
    user_dir.mkdir()
    (user_dir / "custom.yaml").write_text(
        yaml.safe_dump({
            "template": {"id": "custom", "name": "自定义", "description": "d",
                         "placeholders": {"title": {"description": "标题选择器"}}},
            "project": {"name": "custom"},
            "source": {"kind": "static_html", "seeds": ["https://example.com"]},
            "extract": {"mode": "html", "fields": {"t": {"selector": "{{title}}"}}},
        }, allow_unicode=True),
        encoding="utf-8",
    )
    catalog = TemplateCatalog(builtin_dir=tmp_path / "empty", user_dirs=[user_dir])
    # strict=False 时不应把替换值里的 {{other}} 误报为缺失（只有 {{title}} 提供值）
    rendered = catalog.render("custom", {"title": "h1.{{other}}"}, strict=False)
    assert rendered["extract"]["fields"]["t"]["selector"] == "h1.{{other}}"
    # strict=True 且提供所有真实占位符时也应通过（{{other}} 是替换值的一部分，不是模板占位符）
    assert catalog.render("custom", {"title": "h1.{{other}}"}, strict=True)["extract"]["fields"]["t"]["selector"] == "h1.{{other}}"


def test_e15_all_exports_disabled_is_warning_not_error(tmp_path) -> None:
    """E15：关全部导出格式给出 warning，不阻止配置加载。"""
    from omnicrawl.core.config import load_config, validate_config

    cfg = tmp_path / "p.yaml"
    cfg.write_text(
        "project:\n  name: p\n  workspace: work/p\n"
        "source:\n  kind: static_html\n  seeds: [https://example.com]\n"
        "outputs:\n  jsonl: false\n  csv: false\n  xlsx: false\n",
        encoding="utf-8",
    )
    loaded = load_config(cfg)
    errors, warnings = validate_config(loaded)
    assert not errors
    assert any("导出" in w for w in warnings)


def test_e16_apify_home_url_override() -> None:
    """E16：x_twitter 生成 x.com 入口而非 www.x_twitter.com。"""
    from omnicrawl.templates.apify_templates import generate_omnicrawl_template

    template = generate_omnicrawl_template("x_twitter")
    assert "https://x.com/" in template
    assert "www.x_twitter.com" not in template
    # 常规平台仍走默认
    template2 = generate_omnicrawl_template("amazon")
    assert "https://www.amazon.com/" in template2
