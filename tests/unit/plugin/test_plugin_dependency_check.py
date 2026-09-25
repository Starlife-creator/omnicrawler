"""插件声明依赖的可用性预检（#75 §D）。

锁定的性质：
- 声明了、但**确实装不上**的依赖 → 被报出来（可操作提示）；
- 已安装的依赖 → 不报（否则预检变成噪音）；
- **未参与本次 run** 的插件 → 不报（"装了没启用"不该产生警告）；
- 声明形态异常（无 PLUGIN_METADATA / 依赖条目是裸字符串）→ 不崩、按能读到的部分处理；
- 严重度是 **warning 而非 error**：manifest 声明的是依赖全集，不等同本次配置所需。
"""

from __future__ import annotations

import importlib.util
import textwrap
from pathlib import Path

import pytest

from omnicrawler.plugins.plugin_dependency_check import (
    DependencyStatus,
    import_name_candidates,
    plugin_dependency_requirements,
    plugin_dependency_status,
    plugin_dependency_status_for_root,
    plugin_dependency_warnings,
)

pytest.importorskip("yaml")

_MISSING = "omnicrawler_dep_that_does_not_exist_anywhere"


class _Cfg:
    """只实现预检用到的最小配置面（section/source_kind/root/resolve）。"""

    def __init__(self, root: Path, raw: dict) -> None:
        self.root = root
        self.raw = raw

    @property
    def source_kind(self) -> str:
        source = self.raw.get("source", {})
        return str(source.get("kind", "")) if isinstance(source, dict) else ""

    def section(self, name: str) -> dict:
        value = self.raw.get(name, {})
        return value if isinstance(value, dict) else {}

    def resolve(self, value: str) -> Path:
        candidate = Path(value).expanduser()
        return candidate if candidate.is_absolute() else (self.root / candidate).resolve()


def _write_plugin(root: Path, plugin_id: str, deps: list[object]) -> Path:
    plugin_dir = root / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.py").write_text(
        textwrap.dedent(
            f"""
            PLUGIN_METADATA = {{
                "name": "{plugin_id}",
                "version": "1.0.0",
                "dependencies": {deps!r},
            }}


            def handle(operation, payload):
                return {{}}
            """
        ),
        encoding="utf-8",
    )
    return plugin_dir


def _cfg(tmp_path: Path, *, kind: str, plugins: dict | None = None) -> _Cfg:
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir(exist_ok=True)
    raw: dict = {
        "source": {"kind": kind},
        "plugins": {"paths": [str(plugins_dir)], **(plugins or {})},
    }
    return _Cfg(tmp_path, raw)


def test_missing_dependency_is_reported(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, kind="demo")
    _write_plugin(cfg.root / "plugins", "demo", [{"name": _MISSING, "version": ">=1"}])

    findings = plugin_dependency_requirements(cfg)

    assert len(findings) == 1
    plugin_id, candidates, hint = findings[0]
    assert plugin_id == "demo"
    assert candidates[0] == _MISSING
    assert "pip install" in hint


def test_installed_dependency_is_silent(tmp_path: Path) -> None:
    """已安装（用标准库 json 当替身）不该产生任何条目。"""
    cfg = _cfg(tmp_path, kind="demo")
    _write_plugin(cfg.root / "plugins", "demo", [{"name": "json"}])

    assert plugin_dependency_requirements(cfg) == []


def test_unrelated_plugin_is_not_checked(tmp_path: Path) -> None:
    """装了但没参与本次 run 的插件不报——否则预检全是噪音。"""
    plugins_dir = tmp_path / "plugins"
    cfg = _cfg(tmp_path, kind="demo")
    _write_plugin(plugins_dir, "demo", [{"name": "json"}])
    _write_plugin(plugins_dir, "other", [{"name": _MISSING}])

    assert plugin_dependency_requirements(cfg) == []


def test_enabled_market_plugin_is_checked(tmp_path: Path) -> None:
    """已启用的市场插件会随每次 run 执行 ⇒ 它的缺依赖要报（#75 §D）。"""
    plugins_dir = tmp_path / "plugins"
    cfg = _cfg(tmp_path, kind="crawl", plugins={"enabled_market_plugins": ["other"]})
    _write_plugin(plugins_dir, "other", [{"name": _MISSING}])

    findings = plugin_dependency_requirements(cfg)

    assert [item[0] for item in findings] == ["other"]


def test_market_installed_plugin_dir_is_scanned(tmp_path: Path) -> None:
    """市场安装的插件在 ``plugins_installed/<id>/`` ⇒ 必须被扫到。"""
    cfg = _cfg(tmp_path, kind="demo")
    cfg.root.mkdir(parents=True, exist_ok=True)
    _write_plugin(cfg.root / "plugins_installed", "demo", [{"name": _MISSING}])

    findings = plugin_dependency_requirements(cfg)

    assert [item[0] for item in findings] == ["demo"]


def test_plugin_without_metadata_is_skipped(tmp_path: Path) -> None:
    """读不出 PLUGIN_METADATA 时跳过，不让预检崩（插件自身的问题由加载路径报告）。"""
    plugins_dir = tmp_path / "plugins"
    cfg = _cfg(tmp_path, kind="broken")
    plugin_dir = plugins_dir / "broken"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.py").write_text("def register(registry):\n    pass\n", encoding="utf-8")

    assert plugin_dependency_requirements(cfg) == []


def test_string_dependency_entries_are_supported(tmp_path: Path) -> None:
    """兼容以裸字符串声明依赖的插件（归一为 tuple 前的形态）。"""
    cfg = _cfg(tmp_path, kind="demo")
    _write_plugin(cfg.root / "plugins", "demo", [_MISSING])

    assert [item[1][0] for item in plugin_dependency_requirements(cfg)] == [_MISSING]


def test_import_name_candidates_cover_distribution_names() -> None:
    assert import_name_candidates("scikit-learn") == ("scikit-learn", "scikit_learn")
    assert import_name_candidates("PyYAML") == ("PyYAML", "pyyaml")  # 候选去重
    assert import_name_candidates("") == ()
    assert import_name_candidates("httpx") == ("httpx",)


def test_warning_text_names_plugin_and_dependency(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, kind="demo")
    _write_plugin(cfg.root / "plugins", "demo", [{"name": _MISSING}])

    warnings = plugin_dependency_warnings(cfg)

    assert len(warnings) == 1
    assert "demo" in warnings[0] and _MISSING in warnings[0]


def test_preflight_reports_plugin_dependency_as_warning(tmp_path: Path) -> None:
    """端到端：run_preflight 必须把它作为 **warning** 报出，而不是 error 阻断。"""
    from omnicrawler.core.config import DEFAULTS, AppConfig, deep_merge
    from omnicrawler.pipeline_ops.preflight import run_preflight

    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    _write_plugin(plugins_dir, "demo", [{"name": _MISSING}])
    workspace = tmp_path / "work"
    workspace.mkdir()
    raw = deep_merge(
        dict(DEFAULTS),
        {
            "project": {"name": "t", "workspace": "work"},
            "source": {"kind": "demo"},
            "plugins": {"paths": [str(plugins_dir)]},
        },
    )
    config = AppConfig(tmp_path / "task.yaml", tmp_path, raw, workspace)

    report = run_preflight(config)
    checks = report["checks"]
    matched = [item for item in checks if item["code"].startswith("plugin_dependency_demo_")]

    assert matched, checks
    assert matched[0]["status"] == "warning"
    assert _MISSING in matched[0]["message"]


def test_find_spec_guard_is_used(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """反向断言：把探测结果强制为"找不到"，已安装的依赖也必须被报出来。"""
    cfg = _cfg(tmp_path, kind="demo")
    _write_plugin(cfg.root / "plugins", "demo", [{"name": "json"}])
    assert plugin_dependency_requirements(cfg) == []

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(
        "omnicrawler.plugins.plugin_dependency_check.find_spec", lambda name: None, raising=True
    )

    assert [item[1][0] for item in plugin_dependency_requirements(cfg)] == ["json"]


class TestDependencyStatus:
    """决策四「打开即检测」的数据源：**全量**插件只读状态（不按 run 参与者过滤）。"""

    def test_reports_by_plugin_id_including_unrelated(self, tmp_path: Path) -> None:
        """与预检不同：打开即检测要覆盖**所有已装**插件，不限于本次 run 参与者。"""
        plugins_dir = tmp_path / "plugins"
        cfg = _cfg(tmp_path, kind="demo")
        _write_plugin(plugins_dir, "demo", [{"name": "json"}])
        _write_plugin(plugins_dir, "other", [{"name": _MISSING}])

        status = plugin_dependency_status(cfg)

        assert set(status) == {"demo", "other"}
        assert status["demo"].ready is True
        assert status["demo"].missing == ()
        assert status["other"].ready is False
        assert status["other"].missing == (_MISSING,)
        assert status["other"].requirements == (_MISSING,)

    def test_ready_means_no_missing(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path, kind="demo")
        _write_plugin(cfg.root / "plugins", "demo", [{"name": "json"}])

        status = plugin_dependency_status(cfg)

        assert status["demo"].ready
        assert status["demo"].declared == ("json",)

    def test_for_root_scans_without_config(self, tmp_path: Path) -> None:
        """GUI 市场页只有安装根目录 ⇒ 走 root 口径，结论与 config 口径一致。"""
        root = tmp_path / "plugins_installed"
        _write_plugin(root, "demo", [{"name": _MISSING}])

        status = plugin_dependency_status_for_root(root)

        assert status["demo"].missing == (_MISSING,)

    def test_for_root_missing_dir_is_empty(self, tmp_path: Path) -> None:
        assert plugin_dependency_status_for_root(tmp_path / "nope") == {}

    def test_duplicate_id_keeps_richest_status(self, tmp_path: Path) -> None:
        """同一 id 出现在多个路径时，保留信息更全（missing 更多）的一条。"""
        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        _write_plugin(root_a, "dup", [{"name": "json"}])
        _write_plugin(root_b, "dup", [{"name": _MISSING}])

        first = plugin_dependency_status_for_root(root_a)
        second = plugin_dependency_status_for_root(root_b)
        # 分别扫描各自只有一个，合并语义由 plugin_dependency_status 承担——
        # 这里用 config 同时扫两个路径，验证"更全者优先"
        cfg = _cfg(tmp_path, kind="demo", plugins={"paths": [str(root_a), str(root_b)]})
        merged = plugin_dependency_status(cfg)

        assert first["dup"].ready and not second["dup"].ready
        assert merged["dup"].missing == (_MISSING,)

    def test_dataclass_is_frozen(self, tmp_path: Path) -> None:
        status = DependencyStatus(plugin_id="x")
        with pytest.raises(AttributeError):
            status.plugin_id = "y"  # type: ignore[misc]
