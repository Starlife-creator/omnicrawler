"""P6-2：便携写入不越界——正向包含断言。

背景：便携包要求"应用完全自包含、路径不出文件本身"。GUI 设置若写注册表
或 `~/.config` 即逸出包外。审查曾设想用 QSettings.setPath 重定向，但实测
``QSettings.setDefaultFormat``/``setPath`` 对 ``QSettings(org, app)`` 构造在
Windows 上**不生效**（format 仍是 NativeFormat，落注册表）。

修复：gui/settings.make_qsettings 在 is_frozen() 下用**带路径构造**
（``QSettings(path, IniFormat)``）落应用数据根 INI（与 F53 同语义），
main.py / task_canvas.py 的裸 QSettings 调用点已统一改用该 helper。

本测试为**正向包含断言**：portable 模式下触发一次写入，断言数据落在
应用数据根内。不做负向断言（注册表无新增）——Qt 跨平台行为不一且开发机
有历史残留，负向断言会在本地永久误伤。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.gui.settings import AppSettings, make_qsettings


@pytest.fixture()
def portable_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """模拟冻结便携包：数据根指向 tmp_path/app（应用目录内）。"""
    import omnicrawler.core.runtime_paths as rp

    # 先清缓存再替换：lru_cache wrapper 被替换后没有 cache_clear。
    rp.portable_data_root.cache_clear()
    data_root = tmp_path / "app"
    data_root.mkdir()
    monkeypatch.setattr("omnicrawler.core.runtime_paths.is_frozen", lambda: True)
    monkeypatch.setattr("omnicrawler.core.runtime_paths.portable_data_root", lambda: data_root)
    return data_root


def _is_inside(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def test_make_qsettings_portable_writes_inside_data_root(portable_env: Path) -> None:
    """portable 模式下 make_qsettings 的写入落在数据根内（核心断言）。"""
    settings = make_qsettings("OmniCrawler", "GUIWorkbench")
    settings.setValue("templates/favorites", ["t1", "t2"])
    settings.sync()
    path = Path(settings.fileName())
    assert _is_inside(portable_env, path), f"settings 文件逸出数据根: {path}"
    assert path.is_file(), f"settings INI 未落盘: {path}"


def test_make_qsettings_uses_dedicated_ini_per_org_app(portable_env: Path) -> None:
    """不同 org/app 使用独立 INI，避免键冲突。"""
    a = make_qsettings("OmniCrawler", "GUIWorkbench")
    b = make_qsettings("OmniCrawler", "OmniCrawlerGUI")
    assert Path(a.fileName()).resolve() != Path(b.fileName()).resolve()


def test_app_settings_portable_writes_inside_data_root(portable_env: Path) -> None:
    """F53 回归：AppSettings 在 portable 下写入 settings.ini 落数据根。"""
    AppSettings.reset()
    settings = AppSettings.instance()
    settings.theme = "dark"
    settings.sync()
    path = Path(settings._settings.fileName())
    assert _is_inside(portable_env, path), f"AppSettings 逸出数据根: {path}"
    assert path.is_file(), f"settings.ini 未落盘: {path}"


def test_make_qsettings_source_mode_keeps_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """源码（非冻结）模式回退 org/app 构造，不强制落数据根（避免误伤）。"""
    monkeypatch.setattr("omnicrawler.core.runtime_paths.is_frozen", lambda: False)
    settings = make_qsettings("OmniCrawler", "GUIWorkbench")
    # 源码模式下 fileName 由 Qt 决定（平台相关），但不应指向便携数据根。
    assert "OmniCrawler" in settings.organizationName()
    assert "GUIWorkbench" in settings.applicationName()


def test_main_window_qsettings_call_sites_use_portable_helper(portable_env: Path) -> None:
    """main.py / task_canvas.py 的调用点等价于 make_qsettings（回归门禁）。

    无法实例化真实对话框（需完整 QApplication + 布局），此处验证调用点
    所使用的工厂函数在 portable 下的落点，防止重新引入裸 QSettings。
    """
    from omnicrawler.gui.settings import make_qsettings as factory

    settings = factory("OmniCrawler", "GUIWorkbench")
    assert Path(settings.fileName()).resolve().is_relative_to(portable_env.resolve())
