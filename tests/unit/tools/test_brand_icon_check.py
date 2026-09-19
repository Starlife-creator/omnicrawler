"""品牌批次 B2：构建产物品牌图标守卫（`tools/portable_smoke_test.py` 的 B2 段）。

## 这里守的是什么

《优化方案.md》§九附录 A §5 B2 的验收判据，本地可验的部分：

1. **4 个 spec 的 `icon=` 接线**（静态）：两个 Windows spec 的 GUI EXE 各带一行
   `icon=…omnicrawler.ico`，两个 macOS spec 的 BUNDLE 用 `…omnicrawler.icns`
   （且不得残留 `icon=None`）；**Linux spec 必须没有 `icon=`**（PyInstaller 对 ELF
   主动忽略并打告警，写了只会制造噪声）；**CLI / worker / sandbox-host 不得加**
   （§7 #2：控制台工具保留中性图标）。
2. **运行级检查函数**（`_verify_windows_icon` / `_verify_macos_icon`）：构建树与
   归档复跑都经过 `run_smoke_test` ⇒ 守卫在 CI 上对真产物生效。本地能验的是失败形态：
   非 PE 文件必须报错而不是挂死；macOS 假 bundle 的缺 icns / plist 失配必须红。
3. ★ **反向断言**：把任一 spec 的 `icon=` 行删掉 ⇒ 静态用例必须红 ⇒ 字节级还原
   （本文件入库前已实测，见当批提交说明）。
"""

from __future__ import annotations

import importlib.util
import plistlib
import sys
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[3] / "tools" / "portable_smoke_test.py"
_REPO_ROOT = _MODULE_PATH.parents[1]


def _module():
    spec = importlib.util.spec_from_file_location("portable_smoke_test_b2", _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------
# 静态：spec 的 icon= 接线
# --------------------------------------------------------------------------

_WIN_SPECS = ("OmniCrawler.spec", "OmniCrawler-Standard.spec")
_MAC_SPECS = ("OmniCrawler-macOS.spec", "OmniCrawler-macOS-Full.spec")
_LINUX_SPECS = ("OmniCrawler-Linux.spec", "OmniCrawler-Linux-Full.spec")


def _spec_text(name: str) -> str:
    return (_REPO_ROOT / "packaging" / name).read_text(encoding="utf-8")


def test_windows_specs_wire_ico_on_gui_exe_only() -> None:
    for name in _WIN_SPECS:
        text = _spec_text(name)
        icon_lines = [ln for ln in text.splitlines() if "icon=str(" in ln]
        assert len(icon_lines) == 1, f"{name} 应恰好一行 icon=（GUI EXE），实际 {len(icon_lines)} 行"
        assert "omnicrawler.ico" in icon_lines[0], f"{name} 的 icon= 未指向 omnicrawler.ico"


def test_macos_specs_wire_icns_and_no_none_left() -> None:
    for name in _MAC_SPECS:
        text = _spec_text(name)
        assert "icon=None" not in text, f"{name} 仍残留 icon=None"
        icon_lines = [ln for ln in text.splitlines() if "icon=str(" in ln]
        assert len(icon_lines) == 1, f"{name} 应恰好一行 icon=（BUNDLE），实际 {len(icon_lines)} 行"
        assert "omnicrawler.icns" in icon_lines[0]


def test_linux_and_console_exes_do_not_declare_icon() -> None:
    # Linux：PyInstaller 对 ELF 忽略 icon= 并打告警（附录 A §1 事实 #9）。
    for name in _LINUX_SPECS:
        assert "icon=str(" not in _spec_text(name), f"{name} 不应声明 icon="
    # Windows spec 的 icon= 只许出现在 GUI EXE：全文恰一处已由上一条断言保证，
    # 这里再断言它不在 console=True 的 EXE 块内（icon 行必须紧邻 console=False）。
    for name in _WIN_SPECS:
        lines = _spec_text(name).splitlines()
        for index, line in enumerate(lines):
            if "icon=str(" in line:
                window = "\n".join(lines[max(0, index - 8) : index])
                assert "console=False" in window, (
                    f"{name} 的 icon= 不在 GUI EXE（console=False）块附近"
                )


# --------------------------------------------------------------------------
# 运行级：检查函数的失败形态
# --------------------------------------------------------------------------

def test_windows_icon_check_rejects_non_pe_file(tmp_path: Path) -> None:
    """非 PE 文件必须干净报错（GetLastError 路径），不得挂死或误判通过。"""
    if sys.platform != "win32":
        pytest.skip("Windows 专属检查")
    bogus = tmp_path / "OmniCrawler.exe"
    bogus.write_bytes(b"MZ but not really a PE")
    with pytest.raises(RuntimeError, match="brand icon check"):
        _module()._verify_windows_icon(bogus)


def test_windows_icon_check_actually_reads_real_resources(capsys) -> None:
    """正向路径：真带图标的 PE 必须数得出图标。

    ★ 这条是防"守卫空转"的：ctypes 不声明签名时 HMODULE 会被截断，
    枚举恒返回 0 —— 那样构建产物明明带图标也会被判红（假红），
    而若方向反过来写就成了"永远通过"。所以两个方向都要有断言。
    """
    if sys.platform != "win32":
        pytest.skip("Windows 专属检查")
    notepad = Path(r"C:\Windows\System32\notepad.exe")
    if not notepad.is_file():
        pytest.skip("系统内无 notepad.exe，无法做正向路径实测（跳过可见）")
    _module()._verify_windows_icon(notepad)
    out = capsys.readouterr().out
    assert "embeds" in out and "0 icon group(s)" not in out and "0 frame(s)" not in out


def _make_fake_app(root: Path, *, with_icns: bool = True, icon_file: str | None = "omnicrawler") -> Path:
    app = root / "OmniCrawler.app"
    resources = app / "Contents" / "Resources"
    resources.mkdir(parents=True)
    if with_icns:
        (resources / "omnicrawler.icns").write_bytes(b"icns" + b"\x00" * 8)
    info: dict[str, object] = {"CFBundleName": "OmniCrawler"}
    if icon_file is not None:
        info["CFBundleIconFile"] = icon_file
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
    return app.parent


def test_macos_icon_check_accepts_wired_bundle(tmp_path: Path) -> None:
    root = _make_fake_app(tmp_path)
    _module()._verify_macos_icon(root)  # 不抛即通过


def test_macos_icon_check_missing_icns_raises(tmp_path: Path) -> None:
    root = _make_fake_app(tmp_path, with_icns=False)
    with pytest.raises(RuntimeError, match="no .icns"):
        _module()._verify_macos_icon(root)


def test_macos_icon_check_plist_mismatch_raises(tmp_path: Path) -> None:
    root = _make_fake_app(tmp_path, icon_file="some-other-icon")
    with pytest.raises(RuntimeError, match="CFBundleIconFile"):
        _module()._verify_macos_icon(root)
