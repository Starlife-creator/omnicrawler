"""签名闸门：`document_facade` 加载插件前必须验签。

## 这条守卫防的是什么

2026-11-17 发现：facade 用 `spec_from_file_location` 直接 `exec_module`
加载 `plugin.py`，**完全跳过** `plugin_loader` 的 `signature_policy=strict`。

实测后果：一个零签名的 `plugin.py` 会被执行并返回结果。

这不是"少一道检查"。PDF 工作台走的是 **in_process 契约**——插件代码与宿主
**同进程**，权限等于宿主自己。对它跳过验签，等于任何能写插件目录的人
都能在宿主进程里执行任意代码。

## 判据

1. 未签名插件 ⇒ 抛 `RuntimeError`，消息说明原因并给可操作指引；
2. 显式 `OMNICRAWL_PLUGIN_SIGNATURE_POLICY=developer` ⇒ 放行（开发逃生口）；
3. 闸门调用的是宿主既有的 `trust.verify_plugin_trust`，不是自造一套判定。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.services import document_facade

_UNSIGNED_SOURCE = '''"""未签名探针插件。"""
MARKER = "UNSIGNED_PLUGIN_EXECUTED"


def run_operation(operation, payload=None):
    return {"ok": True, "marker": MARKER}
'''


@pytest.fixture
def unsigned_plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """造一个零签名的插件目录，并让 facade 指向它。"""
    directory = tmp_path / "plugins_installed" / "pdf-workbench"
    directory.mkdir(parents=True)
    (directory / "plugin.py").write_text(_UNSIGNED_SOURCE, encoding="utf-8")
    monkeypatch.setenv("OMNICRAWL_PDF_PLUGIN_DIR", str(directory))
    return directory


def test_unsigned_plugin_is_rejected(
    unsigned_plugin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """零签名插件必须被拒绝加载，且提示可操作。"""
    # 显式清掉开发模式：本测试要验的是 strict 路径
    monkeypatch.delenv("OMNICRAWL_PLUGIN_SIGNATURE_POLICY", raising=False)

    with pytest.raises(RuntimeError) as excinfo:
        document_facade.run_operation("pdf.selfcheck", {})

    message = str(excinfo.value)
    assert "签名" in message, f"没说明是签名问题：{message[:200]!r}"
    assert "拒绝" in message or "未通过" in message, f"没明确拒绝：{message[:200]!r}"
    assert "in_process" in message or "同进程" in message, (
        f"没解释为什么要验签：{message[:300]!r}"
    )
    # 关键：不得执行插件代码
    assert "UNSIGNED_PLUGIN_EXECUTED" not in message


def test_developer_mode_allows_unsigned(
    unsigned_plugin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """显式开发模式放行（逃生口存在，但必须显式开启）。"""
    monkeypatch.setenv("OMNICRAWL_PLUGIN_SIGNATURE_POLICY", "developer")
    result = document_facade.run_operation("pdf.selfcheck", {})
    assert result.get("marker") == "UNSIGNED_PLUGIN_EXECUTED", (
        "开发模式下插件没被执行"
    )


def test_gate_uses_host_trust_verifier(
    unsigned_plugin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """闸门必须复用宿主的 `trust.verify_plugin_trust`，不自造判定。

    做法：patch 掉 `verify_plugin_trust`，返回"维护者签名"，
    则未签名插件也应被放行——证明闸门真的走了那个函数。
    """
    monkeypatch.delenv("OMNICRAWL_PLUGIN_SIGNATURE_POLICY", raising=False)

    from omnicrawler.plugins import trust as trust_model

    class _Decision:
        level = trust_model.TrustLevel.MaintainerSigned
        reason = "测试桩：假装维护者签名"

    monkeypatch.setattr(
        trust_model, "verify_plugin_trust", lambda *a, **k: _Decision()
    )

    result = document_facade.run_operation("pdf.selfcheck", {})
    assert result.get("marker") == "UNSIGNED_PLUGIN_EXECUTED", (
        "闸门没有调用 trust.verify_plugin_trust（打桩后仍被拒绝）"
    )
