"""`tests/unit/tools` 的公共装配。

## 为什么要在测试里声明 `developer` 签名策略

2026-11-17 给 `document_facade` 的插件加载补了**签名闸门**（此前被绕过，
零签名的 plugin.py 会被直接执行——in_process 契约下等于把宿主交出去）。

闸门默认 strict：只接受维护者签名或本机信任列表里的创作者签名。
但**本仓库内的插件测试**用的是未签名的源码目录（`submissions/...`），
默认为它们签名既无必要也不该做（私钥不入仓）。

所以测试统一声明开发模式。这与生产行为的差异**仅限验签这一项**：

* 闸门本身仍然执行、仍然调用 `verify_plugin_trust`；
* 生产（不设该变量）依旧 strict 拒载未签名插件。

另有一组专门测闸门的用例（`test_facade_signature_gate.py`）会显式
清掉这个变量，确保"拒绝"那条路径真的被覆盖到。
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _allow_unsigned_plugin_in_development(monkeypatch: pytest.MonkeyPatch) -> None:
    """开发模式：让本仓库的未签名插件可加载（生产仍 strict）。"""
    monkeypatch.setenv("OMNICRAWL_PLUGIN_SIGNATURE_POLICY", "developer")
