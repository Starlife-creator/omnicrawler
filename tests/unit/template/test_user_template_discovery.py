"""B4a：用户/市场安装模板的跨入口发现守卫。

背景：此前只有 GUI 发现 ``<project>/templates`` 与 ``<project>/templates_installed``，
CLI（commands/template.py）、doctor、pipeline/_extract 都只看 bundled ——
市场安装的模板 CLI 完全不可见，而 tools/market.py 却提示"GUI/CLI 将自动发现"。

本文件钉三件事（均可反向触发：把调用点改回 ``bundled_template_catalog()`` 即红）：
1. ``user_template_dirs`` 的目录契约（与 GUI 同一组目录名）；
2. CLI ``templates list`` 必须能看到 templates_installed 里的模板；
3. doctor 的模板存在性校验必须把市场安装 id 视为"存在"。
"""

from __future__ import annotations

from pathlib import Path

from omnicrawler.commands import template as template_cmd
from omnicrawler.templates.template_catalog import TemplateCatalog, user_template_dirs

# 与市场安装落盘形态一致的合法模板（template 块 + project + source）
_INSTALLED_TEMPLATE_YAML = """\
template:
  id: sites/market/probe-template
  name: 市场探针模板
  category: sites/market
  description: 仅测试用的市场安装模板
  version: 1.0.0
project: {name: market_probe, workspace: work/market_probe}
source: {kind: static_html, seeds: ['https://example.org/']}
"""


def _install_probe(root: Path, subdir: str = "templates_installed") -> Path:
    target = root / subdir / "sites" / "market" / "probe-template"
    target.mkdir(parents=True, exist_ok=True)
    (target / "template.yaml").write_text(_INSTALLED_TEMPLATE_YAML, encoding="utf-8")
    return target


def test_user_template_dirs_contract() -> None:
    """目录契约：与 GUI（gui/main.py）同一组目录名，改动必须两处同步。"""
    assert user_template_dirs(None) == ()
    dirs = user_template_dirs(Path("Z:/proj"))
    assert dirs == (Path("Z:/proj/templates"), Path("Z:/proj/templates_installed"))


def test_cli_templates_list_sees_market_installed_template(
    tmp_path: Path, monkeypatch
) -> None:
    """反向断言载体：调用点若改回 bundled_template_catalog()，本用例必红。"""
    _install_probe(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = template_cmd.execute("list")
    ids = {
        item["id"]
        for items in result["categories"].values()
        for item in items
    }
    assert "sites/market/probe-template" in ids, (
        "CLI templates list 必须能看到 templates_installed 里的市场模板"
    )
    assert result["user"] >= 1


def test_cli_templates_list_without_user_dirs_stays_bundled_only(
    tmp_path: Path, monkeypatch
) -> None:
    """负向对照：没有用户目录时不虚报 user 计数（防"恒真"守卫）。"""
    monkeypatch.chdir(tmp_path)
    result = template_cmd.execute("list")
    assert result["user"] == 0
    assert result["builtin"] == result["total"]


def test_catalog_resolves_installed_id_for_doctor_style_checks(tmp_path: Path) -> None:
    """doctor/映射校验的判据形态：catalog.get(市场 id) 必须非 None。"""
    _install_probe(tmp_path)
    catalog = TemplateCatalog(
        Path(__file__).resolve().parents[3] / "src" / "omnicrawler" / "templates",
        user_template_dirs(tmp_path),
    )
    assert catalog.get("sites/market/probe-template") is not None
    assert catalog.get("generic/list-detail") is not None, "内置模板不受影响"
