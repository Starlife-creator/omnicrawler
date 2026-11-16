"""条目 11：OCR 引擎可插拔，而不是写死两个名字。

**为什么必须放开**

当前引擎在**三处**被硬编码为 `paddle` / `tesseract`：

| 位置 | 现状 |
|---|---|
| `pdfx/ocr.py` `create_backend()` | `if name == "paddle" … if name == "tesseract"`，其余 raise |
| `pdfx/config.py:255` | `backend not in {"none","paddle","tesseract","auto"}` ⇒ raise |
| `services/component_manager.py:102` | `runtime.get("engine") not in {"paddle","tesseract"}` ⇒ raise |

后果：接入任何新引擎（例如 Apache-2.0 的 Xiaomi-OCR-0）都必须改源码重发版，
而组件的 `engine` 白名单还让新引擎**连装都装不进去**。

**判据（本守卫要钉住的四件事）**

1. **注册优先**：外部可通过注册接口增加引擎，不必改这三处写死的地方；
2. **内置仍在**：`paddle` / `tesseract` 的内置注册不能丢——放开不等于拆除；
3. **未知引擎显式失败**：没注册的名字必须报**可枚举**的错误（列出已知引擎），
   不能静默回落到某个引擎（那会让用户以为跑的是他选的引擎）；
4. **`auto` 语义不变**：自动解析仍然只在**真正可用**的引擎里挑，
   注册表里有但不具备运行时条件的引擎不能被「自动」选中。

第 3、4 条是**防止放开后出现新的静默错误**——这正是本条最大的风险。
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_OCR = _REPO_ROOT / "src" / "omnicrawler" / "pdfx" / "ocr.py"


def test_registry_api_exists() -> None:
    """`pdfx.ocr` 必须提供引擎注册/查询接口。"""
    from omnicrawler.pdfx import ocr

    assert hasattr(ocr, "register_ocr_engine"), "缺少 register_ocr_engine"
    assert hasattr(ocr, "registered_ocr_engines"), "缺少 registered_ocr_engines（要能枚举）"
    assert hasattr(ocr, "resolve_ocr_engine"), "缺少 resolve_ocr_engine（统一解析入口）"


def test_builtin_engines_are_registered() -> None:
    """放开不等于拆除：内置两个引擎必须仍在注册表里。"""
    from omnicrawler.pdfx.ocr import registered_ocr_engines

    engines = registered_ocr_engines()
    assert "paddle" in engines, "内置 paddle 引擎丢了"
    assert "tesseract" in engines, "内置 tesseract 引擎丢了"


def test_unknown_engine_fails_with_enumeration() -> None:
    """未注册的引擎必须报错，且错误里列出已知引擎（可操作）。"""
    from omnicrawler.pdfx.ocr import resolve_ocr_engine

    with pytest.raises(Exception) as excinfo:
        resolve_ocr_engine("no-such-engine")
    message = str(excinfo.value)
    assert "no-such-engine" in message, f"错误没提到请求的引擎名：{message!r}"
    # 必须能据此知道"现在有哪些可选"，否则用户只能猜
    assert "paddle" in message and "tesseract" in message, (
        f"错误没有枚举已知引擎，用户无从知道可选什么：{message!r}"
    )


def test_registered_engine_can_be_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    """注册一个假引擎后，必须能被解析出来——这是"可插拔"的最小证明。"""
    from omnicrawler.pdfx import ocr

    sentinel = object()

    def _factory(config: dict) -> object:
        return sentinel

    ocr.register_ocr_engine("fake-engine", _factory)
    try:
        assert "fake-engine" in ocr.registered_ocr_engines()
        resolved = ocr.resolve_ocr_engine("fake-engine")
        assert resolved is sentinel
    finally:
        ocr._unregister_ocr_engine_for_tests("fake-engine")  # type: ignore[attr-defined]


def test_auto_does_not_pick_registered_but_unavailable_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`auto` 只能在**真正可用**的引擎里挑。

    注册表里有某个引擎 ≠ 本机装得了它。若 auto 选中了不可用的引擎，
    用户会在运行到一半才发现没有依赖——这正是条目 2 要根除的那类静默失败。
    """
    from omnicrawler.pdfx import ocr

    # 注册一个"永远不可用"的引擎，并让内置两个都不可用
    ocr.register_ocr_engine("ghost-engine", lambda config: object(), available=lambda: False)
    monkeypatch.setattr(ocr, "_paddle_ready", lambda: False)
    monkeypatch.setattr(ocr, "_tesseract_ready", lambda: False)
    try:
        resolved, reason = ocr.resolve_auto_ocr_backend()
        assert resolved == "none", f"auto 选中了不可用的引擎：{resolved}"
        assert reason, "必须给出可操作原因"
        assert "ghost-engine" not in resolved
    finally:
        ocr._unregister_ocr_engine_for_tests("ghost-engine")  # type: ignore[attr-defined]


def test_component_engine_whitelist_is_driven_by_registry() -> None:
    """组件的 engine 白名单必须来自注册表，而不是写死两个字面量。

    判据是**源码级**的：`component_manager.py` 里不应再出现
    `{"paddle", "tesseract"}` 这种写死集合。
    """
    path = _REPO_ROOT / "src" / "omnicrawler" / "services" / "component_manager.py"
    text = path.read_text(encoding="utf-8")
    hardcoded = '{"paddle", "tesseract"}'
    assert hardcoded not in text, (
        "component_manager 仍写死引擎白名单 "
        f"{hardcoded}；新引擎装不进去。应改为查注册表。"
    )


def test_config_validation_error_enumerates_engines() -> None:
    """配置校验的报错要有用：不能只写"只能是 none、paddle 或 tesseract"。"""
    text = _OCR.parent.joinpath("config.py").read_text(encoding="utf-8")
    # 允许保留字面量作为"内置"说明，但错误信息必须能反映注册表；
    # 这里做弱判据：至少要提到 registered/引擎列表来源。
    assert ("registered_ocr_engines" in text) or ("ocr_engine_names" in text), (
        "pdfx/config.py 的校验没有引用注册表；新增引擎会在配置层被直接拒绝"
    )
