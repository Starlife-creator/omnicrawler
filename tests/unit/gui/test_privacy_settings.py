"""C-#3：隐私开关设置层默认值与持久化契约。

覆盖范围仅限 ``AppSettings`` 设置层：默认口径、getter/setter 往返与布尔收敛。
日志脱敏读取、崩溃队列/上报等**消费链路**落在归属外模块（``core/logging_utils.py``、
``delegates/error_dialog.py`` 等），由集成步骤统一接线，不在本测试内模拟。

运行环境：需 Qt offscreen（``QT_QPA_PLATFORM=offscreen``），与其余 ``tests/unit/gui``
用例一致；headless 沙箱未装 PySide6 时跳过执行，待 GUI CI 环境复核。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.gui.settings import AppSettings


@pytest.fixture(autouse=True)
def _reset_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """把设置层落到 tmp_path 内的 INI，不碰开发者真实配置。

    源码模式下 ``QSettings("OmniCrawler", "OmniCrawlerGUI")`` 落 HKCU 注册表，而本文件
    的往返用例会写 ``privacy/redact_enabled`` 且从不还原 ⇒ 跑一次测试就把用户日志脱敏
    开关关掉（下一趟真实运行就不再脱敏）。Windows 上 ``setPath``/``setDefaultFormat``
    对这个构造不生效（实测见 test_portable_write_boundary.py），所以借已测的
    ``is_frozen()`` 便携分支把落点改到 tmp_path。
    """
    from omnicrawler.core import runtime_paths

    monkeypatch.setattr(runtime_paths, "is_frozen", lambda: True)
    monkeypatch.setattr(runtime_paths, "portable_data_root", lambda: tmp_path)
    AppSettings.reset()
    yield
    AppSettings.reset()


def test_privacy_defaults_match_product_policy() -> None:
    """默认口径：脱敏开启；崩溃上报关闭（当前版本无遥测通道）。"""
    settings = AppSettings.instance()
    assert settings.privacy_redact_enabled is True
    assert settings.crash_report_enabled is False


def test_privacy_redact_setter_persists_across_instance() -> None:
    settings = AppSettings.instance()
    settings.privacy_redact_enabled = False
    settings.sync()
    # 重新取单例后应从持久化层读回同一值。
    AppSettings.reset()
    reopened = AppSettings.instance()
    assert reopened.privacy_redact_enabled is False


def test_crash_report_setter_persists_and_coerces_bool() -> None:
    settings = AppSettings.instance()
    settings.crash_report_enabled = True
    assert settings.crash_report_enabled is True

    settings.crash_report_enabled = 0  # 非布尔输入收敛为 False
    assert settings.crash_report_enabled is False
