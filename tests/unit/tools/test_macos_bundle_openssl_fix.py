"""macOS 便携包 `_ssl` 的 OpenSSL 依赖修正脚本：**本地可验**的那部分（W4.1）。

事故（2026-09-16，CI `35089212498`）：Full 版产物里 `_ssl` 的 `libcrypto` 依赖被绑定到
**cv2 自带的同名副本**（`Contents/Frameworks/cv2/.dylibs/libcrypto.3.dylib`）⇒ 缺
`_X509_STORE_get1_objects` 符号 ⇒ `ssl` 不可用。修复脚本要**只在这种"指向别的包副本"的场景**
才动手，不能误改正常依赖 —— 那条判断就是本文件守的东西（`otool`/`install_name_tool` 部分只能在
macOS 上跑，这里不假装验证）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parents[3] / "tools" / "fix_macos_bundle_openssl.py"


def _module():
    spec = importlib.util.spec_from_file_location("fix_macos_bundle_openssl", _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_detects_foreign_vendored_copy(tmp_path: Path) -> None:
    """★ 核心：指向别的包自带 `.dylibs/` 的依赖必须被判为"外来副本"。"""
    module = _module()
    foreign = (
        "/tmp/x/OmniCrawler.app/Contents/Frameworks/cv2/.dylibs/libcrypto.3.dylib"
    )
    assert module._is_foreign("libcrypto.3.dylib", foreign) is True


def test_does_not_touch_normal_dependencies() -> None:
    """正常依赖不得被判为外来（否则会误改、破坏产物）。"""
    module = _module()
    normal = "/usr/lib/libcrypto.3.dylib"
    assert module._is_foreign("libcrypto.3.dylib", normal) is False
    # 名字对不上（例如依赖写成 @rpath 而未解析）也不动
    assert module._is_foreign("libssl.3.dylib", "/opt/homebrew/lib/libcrypto.3.dylib") is False


def test_finds_the_ssl_module_in_a_bundle(tmp_path: Path) -> None:
    module = _module()
    so = tmp_path / "Contents" / "Frameworks" / "python3.12" / "lib-dynload" / "_ssl.cpython-312-darwin.so"
    so.parent.mkdir(parents=True)
    so.write_bytes(b"\x00")
    assert module._find_ssl_module(tmp_path) == so


def test_missing_module_is_reported_not_silently_passed(tmp_path: Path) -> None:
    """产物里没有 `_ssl` 时返回非零（不许静默成功）。"""
    module = _module()
    assert module.repair(tmp_path) == 2


def test_repair_reports_missing_sources_instead_of_pretending(tmp_path: Path, monkeypatch) -> None:
    """构建期找不到可替换的库时返回非零 —— 让 CI 当场红，而不是产出 ssl 坏的包。"""
    module = _module()
    so = tmp_path / "Contents" / "Frameworks" / "python3.12" / "lib-dynload" / "_ssl.cpython-312-darwin.so"
    so.parent.mkdir(parents=True)
    so.write_bytes(b"\x00")
    monkeypatch.setattr(
        module, "_dependencies", lambda _m: {"libcrypto.3.dylib": "/x/cv2/.dylibs/libcrypto.3.dylib"}
    )
    monkeypatch.setattr(module, "_source_libs", lambda: {})
    assert module.repair(tmp_path) == 3


def test_source_libs_returns_empty_mapping_instead_of_raising(tmp_path: Path, monkeypatch) -> None:
    """★ 实测教训（CI `35095464325`）：`actions/setup-python` 的 framework Python 里
    `sys.prefix/lib` **没有** OpenSSL（它们在 Homebrew 的 openssl@3 下）。

    因此这条守的是：**找不到时返回空映射、绝不抛异常** —— 异常会让错误信息丢失，
    而"空映射"会由 `repair()` 转成明确的非零退出（那一轮 CI 就是这样给出
    "构建期 Python 下找不到 libcrypto/libssl（无法提供正确副本）"的）。
    """
    module = _module()
    monkeypatch.setattr(module.sys, "prefix", str(tmp_path / "empty-prefix"))
    monkeypatch.setattr(module.sys, "base_prefix", str(tmp_path / "empty-base"))
    result = module._source_libs()
    assert isinstance(result, dict)
