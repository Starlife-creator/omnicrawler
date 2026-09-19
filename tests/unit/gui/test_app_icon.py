"""品牌批次 B1：应用自身图标（app_icon）接线与资产的验收断言。

对应《优化方案.md》§九附录 A §5 B1 的判据：

1. :func:`omnicrawler.gui.app_icon.app_icon` 返回的 ``QIcon`` 非空，且 ``availableSizes()``
   至少包含 16/32/256 —— **空 QIcon 必须判红**（这正是"图标没接上"的失败形态）；
2. ``gui/main.py`` 源码级断言：``setWindowIcon`` 与 ``_tray_icon.setIcon(`` 两处调用真实存在
   （防止日后重构删掉）；``setDesktopFileName("omnicrawler")`` 亦在列（触点 2，I1b 联动）；
3. 6 个 ``packaging/*.spec`` 都声明了 ``gui/branding`` 的 ``datas`` 行 ——
   ``app_icon()`` 对缺失文件静默跳过（设计如此），因此"spec 漏加一行"不会被运行时报错发现，
   只能在这里被断言出来；
4. 9 个品牌数据文件在位且结构正确（PNG 阶梯逐文件核对 IHDR 尺寸，ICO/ICNS 验魔数）；
5. 失败形态文档化：解析根不存在时 ``app_icon()`` 静默返回空图标（契约，不抛异常）。

反向断言（承重性探针）在本文件入库前已实测：把 ``app_icon._DIR`` 段名改错
→ 用例 1 变红（QIcon 为空）→ 字节级还原。见当批提交说明。
"""

from __future__ import annotations

import importlib.util
import struct
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None, reason="GUI 测试需要 PySide6"
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BRANDING = _REPO_ROOT / "src" / "omnicrawler" / "gui" / "branding"

_LADDER_SIZES = (16, 24, 32, 48, 64, 128, 256)
_BRANDING_FILES = (
    "omnicrawler.ico",
    "omnicrawler.icns",
    *(f"omnicrawler-icon-{size}.png" for size in _LADDER_SIZES),
)

_APP = None


def _app():
    global _APP
    if _APP is None:
        from PySide6.QtWidgets import QApplication

        _APP = QApplication.instance() or QApplication([])
    return _APP


def test_app_icon_is_not_empty_with_expected_sizes() -> None:
    """空 QIcon 必须判红——这是"图标没接上"的失败形态（判据 ①）。"""
    from omnicrawler.gui.app_icon import app_icon

    _app()
    icon = app_icon()
    assert not icon.isNull(), (
        "app_icon() 返回空 QIcon：gui/branding/ 下的图标文件缺失或不可解码，"
        "窗口与托盘将回退为系统默认图标"
    )
    sizes = {s.width() for s in icon.availableSizes()}
    for required in (16, 32, 256):
        assert required in sizes, f"QIcon 缺少 {required}px 一档，实际含 {sorted(sizes)}"


def test_branding_assets_present_and_structurally_valid() -> None:
    """9 个数据文件在位；PNG 逐文件核对 IHDR 尺寸（防"拿错文件"）。"""
    for name in _BRANDING_FILES:
        path = _BRANDING / name
        assert path.is_file(), f"缺少品牌数据文件 {name}"
        data = path.read_bytes()
        assert data, f"{name} 是空文件"
        if name.endswith(".png"):
            assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{name} 不是 PNG"
            expected = int(name.rsplit("-", 1)[1].split(".")[0])
            width, height = struct.unpack(">II", data[16:24])
            assert (width, height) == (expected, expected), (
                f"{name} 的 IHDR 尺寸是 {width}x{height}，文件名声明 {expected}x{expected}"
            )
        elif name.endswith(".ico"):
            # ICO 魔数：保留字 0x0000 + 类型 0x0001（icon）
            assert data[:4] == b"\x00\x00\x01\x00", f"{name} 的 ICO 魔数不符"
        elif name.endswith(".icns"):
            assert data[:4] == b"icns", f"{name} 的 ICNS 魔数不符"


def test_main_py_wires_the_icon_calls() -> None:
    """源码级断言：main.py 里的三处接线真实存在（判据 ③，防日后重构删掉）。"""
    source = (_REPO_ROOT / "src" / "omnicrawler" / "gui" / "main.py").read_text(encoding="utf-8")
    assert "app.setWindowIcon(app_icon())" in source, "main.py 缺少 setWindowIcon 接线"
    assert "self._tray_icon.setIcon(" in source, "main.py 缺少托盘 setIcon 接线"
    assert 'app.setDesktopFileName("omnicrawler")' in source, (
        "main.py 缺少 setDesktopFileName（Wayland app_id / .desktop 匹配，触点 2）"
    )


def test_all_six_specs_declare_branding_datas() -> None:
    """6 个 spec 都要带 branding 的 datas 行（app_icon 静默跳过缺失文件 ⇒ 只能在这里断言）。"""
    specs_dir = _REPO_ROOT / "packaging"
    spec_names = (
        "OmniCrawler.spec",
        "OmniCrawler-Standard.spec",
        "OmniCrawler-Linux.spec",
        "OmniCrawler-Linux-Full.spec",
        "OmniCrawler-macOS.spec",
        "OmniCrawler-macOS-Full.spec",
    )
    assert spec_names and len(spec_names) == 6
    offenders: list[str] = []
    for name in spec_names:
        path = specs_dir / name
        assert path.is_file(), f"spec 文件不存在：{name}（清单与 packaging/ 脱节，本用例必须更新）"
        text = path.read_text(encoding="utf-8")
        line = '(str(src_root / "omnicrawler" / "gui" / "branding"), "omnicrawler/gui/branding"),'
        if line not in text:
            offenders.append(name)
    assert not offenders, f"以下 spec 缺少 gui/branding 的 datas 行：{offenders}"


def test_app_icon_missing_root_returns_null_icon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """契约：解析根不存在时静默返回空 QIcon（不抛异常）——失败形态本身被固定下来。"""
    from omnicrawler.gui import app_icon as app_icon_module

    monkeypatch.setattr(
        app_icon_module, "package_resource", lambda *parts: tmp_path / "does-not-exist"
    )
    icon = app_icon_module.app_icon()
    assert icon.isNull(), "解析根不存在时应返回空 QIcon（静默跳过契约），却拿到了非空图标"
