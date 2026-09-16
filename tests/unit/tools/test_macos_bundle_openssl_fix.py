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


def test_pin_judgement_is_about_our_copy_not_appearance() -> None:
    """★ 核心判据：**只看"是否已指向我们放的副本"**，不看"看起来是否外来"。

    教训（CI `35101702777`）：第一版按"绝对路径里带 `/.dylibs/`"判断 ⇒ 而 `otool -L`
    显示的是 `@rpath/libcrypto.3.dylib` ⇒ 被判"正常"、脚本什么也没做，运行时 `@rpath`
    仍旧解析到 **cv2 自带的副本**，错误照旧。`@rpath` 的解析结果**不可信**。
    """
    module = _module()
    assert module._already_pinned("@loader_path/libcrypto.3.dylib", "libcrypto.3.dylib") is True
    # 常见形态都必须被判成"还没钉住"（⇒ 需要动手）
    for original in ("@rpath/libcrypto.3.dylib", "/usr/lib/libcrypto.3.dylib",
                     "/x/cv2/.dylibs/libcrypto.3.dylib"):
        assert module._already_pinned(original, "libcrypto.3.dylib") is False, original


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
