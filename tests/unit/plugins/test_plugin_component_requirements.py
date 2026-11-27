"""组件能力需求的**运行前预检**（2026-11-27）。

## 它补的是哪个洞

`services/component_capability.py` 里三件东西早就写好了：

| 函数 | 谁该调它 | 实际状态（修前）|
|---|---|---|
| `resolve()` | 市场门禁 / 预检 | **零调用方** |
| `check_market()` | 市场发版门禁 | **文件里根本没有这个 def**（本轮补上）|
| `install_hint()` | 宿主预检 | **零调用方** |

于是「插件要 `ocr.paddle`、但本机没装提供它的组件」这件事，用户只能在**运行到一半**
时撞 `ModuleNotFoundError` 才知道。

## 为什么是 warning 不是 error

与「插件依赖」同理（见 `plugin_dependency_check` 模块 docstring）：组件可能让用户
在市场里现装，把能跑的 run 挡在门外只会让人学会忽略预检。

★ 与市场侧 `check_market()` 的分工：那里管「**发布时**有没有组件兜底」，
  这里管「**用户机器上**装没装」。两者缺一不可。
"""

from __future__ import annotations

import json
from pathlib import Path

from omnicrawler.core.config import AppConfig
from omnicrawler.pipeline_ops.preflight import run_preflight
from omnicrawler.plugins.plugin_dependency_check import plugin_component_requirements


def _config(tmp_path: Path, *, plugin_id: str = "doc-workbench") -> AppConfig:
    raw = {
        "project": {"name": "x", "workspace": str(tmp_path / "work")},
        "source": {"kind": plugin_id, "seeds": ["https://example.com/"]},
        "plugins": {"enabled_market_plugins": [plugin_id]},
        "http": {"delay_seconds": 0.0},
    }
    return AppConfig(tmp_path / "c.yaml", tmp_path, raw, tmp_path / "work")


def _write_plugin(tmp_path: Path, *, capabilities: list[str], name: str = "doc-workbench") -> None:
    directory = tmp_path / "plugins_installed" / name
    directory.mkdir(parents=True, exist_ok=True)
    metadata = {
        "name": name,
        "version": "1.0.0",
        "requires_components": capabilities,
        "permissions": [],
    }
    (directory / "plugin.py").write_text(
        "PLUGIN_METADATA = " + json.dumps(metadata) + "\n"
        "def handle(operation, payload):\n"
        "    return {}\n",
        encoding="utf-8",
    )


def _install_component(tmp_path: Path, component_id: str, provides: list[str]) -> None:
    directory = tmp_path / "work" / "components"
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / "installed.json"
    document: dict = {}
    if manifest.is_file():
        document = json.loads(manifest.read_text(encoding="utf-8"))
    document[component_id] = {"name": component_id, "provides": provides}
    manifest.write_text(json.dumps(document), encoding="utf-8")


# ── 无需求 ──────────────────────────────────────────────────────────────


def test_plugin_without_component_needs_is_silent(tmp_path: Path) -> None:
    """没有 `requires_components` 的插件不该产生任何噪音。"""
    _write_plugin(tmp_path, capabilities=[])
    assert plugin_component_requirements(_config(tmp_path)) == []


def test_unknown_plugin_is_not_reported(tmp_path: Path) -> None:
    """只报**本次运行会用到**的插件（装了没启用不该报）。"""
    _write_plugin(tmp_path, capabilities=["ocr.paddle"], name="other-plugin")
    assert plugin_component_requirements(_config(tmp_path)) == []


# ── 核心：缺组件要报，且说清装哪个 ──────────────────────────────────────


def test_missing_component_is_reported_with_an_install_hint(tmp_path: Path) -> None:
    """★ 核心判据：缺组件 ⇒ 报一条，且提示里点名缺的能力。"""
    _write_plugin(tmp_path, capabilities=["ocr.paddle"])
    findings = plugin_component_requirements(_config(tmp_path))
    assert len(findings) == 1
    plugin_id, missing, hint = findings[0]
    assert plugin_id == "doc-workbench"
    assert missing == ("ocr.paddle",)
    assert "ocr.paddle" in hint


def test_installed_component_silences_the_warning(tmp_path: Path) -> None:
    """装上了提供该能力的组件 ⇒ 不再报。"""
    _write_plugin(tmp_path, capabilities=["ocr.paddle"])
    _install_component(tmp_path, "ocr-paddle", ["ocr.paddle"])
    assert plugin_component_requirements(_config(tmp_path)) == []


def test_component_providing_a_different_capability_does_not_silence_it(
    tmp_path: Path,
) -> None:
    """★ 反方向：装了**别的**组件不算满足。

    这条防的是「只要有组件就不报」这种偷懒实现。
    """
    _write_plugin(tmp_path, capabilities=["ocr.paddle"])
    _install_component(tmp_path, "convert-markdown", ["convert.markdown"])
    findings = plugin_component_requirements(_config(tmp_path))
    assert len(findings) == 1 and findings[0][1] == ("ocr.paddle",)


def test_broken_installed_manifest_is_treated_as_nothing_installed(tmp_path: Path) -> None:
    """清单读不了 ⇒ 当「一个都没装」，不抛异常（预检不该因此失败）。"""
    _write_plugin(tmp_path, capabilities=["ocr.paddle"])
    directory = tmp_path / "work" / "components"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "installed.json").write_text("{ not json", encoding="utf-8")
    findings = plugin_component_requirements(_config(tmp_path))
    assert len(findings) == 1


def test_manifest_only_plugin_yaml_is_supported(tmp_path: Path) -> None:
    """`plugin.yaml` 形态（无 plugin.py）也要能读出组件需求。"""
    import pytest

    pytest.importorskip("yaml")
    directory = tmp_path / "plugins_installed" / "doc-workbench"
    directory.mkdir(parents=True)
    (directory / "plugin.yaml").write_text(
        "name: doc-workbench\nversion: 1.0.0\nrequires_components:\n  - ocr.paddle\n",
        encoding="utf-8",
    )
    findings = plugin_component_requirements(_config(tmp_path))
    assert len(findings) == 1 and findings[0][1] == ("ocr.paddle",)


# ── 接进 preflight ──────────────────────────────────────────────────────


def test_preflight_surfaces_the_warning_without_blocking(tmp_path: Path) -> None:
    """★ 接线的判据：`run_preflight` 要出现这条 warning，且 `ok` **不受影响**。

    `ok` 必须仍为 True —— 缺组件是可修复的运行前提示，不是配置错误。
    """
    _write_plugin(tmp_path, capabilities=["ocr.paddle"])
    result = run_preflight(_config(tmp_path))
    component_checks = [
        item for item in result["checks"] if item["code"].startswith("plugin_component_")
    ]
    assert component_checks, f"预检没有报组件缺失：{[c['code'] for c in result['checks']]}"
    check = component_checks[0]
    assert check["status"] == "warning"
    assert check["fix"]["action"] == "install_component"
    assert check["fix"]["missing_capabilities"] == ["ocr.paddle"]
    assert result["warnings"] >= 1


def test_preflight_does_not_flag_when_component_is_installed(tmp_path: Path) -> None:
    _write_plugin(tmp_path, capabilities=["ocr.paddle"])
    _install_component(tmp_path, "ocr-paddle", ["ocr.paddle"])
    result = run_preflight(_config(tmp_path))
    assert not [c for c in result["checks"] if c["code"].startswith("plugin_component_")]
