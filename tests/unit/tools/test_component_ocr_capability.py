"""契约扩展：`component.ocr.recognize` 的边界与安全性（PDF 插件化批次 1）。

**为什么这个能力必须存在**

PDF 工作台正在被迁成**插件**（契约 2，子进程）。它原先会自己调用已验签的
OCR 组件（`ComponentOCRBackend`）。但组件调用涉及三件**宿主特权**：

1. 读 `configs/component_trust.pub.pem` 验签；
2. 拿跨进程注册表锁做租赁；
3. 裁剪环境变量后启动组件子进程。

插件是受沙箱约束的子进程。**让它自己做这三件事，等于把安全边界下移到
不受控进程里** —— 与「安全与隔离」原则直接冲突。

所以新增 `component.ocr.recognize`：插件只交 PNG 与引擎名，
宿主完成验签/租赁/调用，回传文本。**插件永远看不到组件路径、信任根、注册表。**

**本文件钉住的判据**

1. 能力已注册（版本表 + 权限映射），且权限是**独立新权限** `component:ocr`
   —— 不复用 `files:read` 等既有权限（那样授予面会意外变大）；
2. 未声明该权限的插件**调不动**它（fail-closed）；
3. 输入校验在**宿主侧再做一次**：PNG 魔数、大小上限、base64 合法性；
4. 能力归类为**高风险**（它会让宿主代为执行外部可执行文件）；
5. 支柱②（plugins）**不得**直接 import `pdfx` —— 必须经 facade。
"""

from __future__ import annotations

import base64
import inspect
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = _REPO_ROOT / "src" / "omnicrawler"


def test_capability_registered_with_versions_and_permissions() -> None:
    """能力必须同时登记在版本表与权限映射里。"""
    from omnicrawler.plugins.plugin_broker_contracts import (
        _CAPABILITY_PERMISSIONS,
        CAPABILITY_VERSIONS,
    )

    assert "component.ocr.recognize" in CAPABILITY_VERSIONS
    assert CAPABILITY_VERSIONS["component.ocr.recognize"] == 1
    assert _CAPABILITY_PERMISSIONS["component.ocr.recognize"] == "component:ocr"


def test_permission_is_allowlisted() -> None:
    """新权限必须在沙箱白名单里，否则合法插件也装不上。"""
    from omnicrawler.plugins.plugin_sandbox import ALLOWED_PERMISSIONS

    assert "component:ocr" in ALLOWED_PERMISSIONS


def test_permission_is_high_risk() -> None:
    """它会让宿主代为启动外部可执行文件 ⇒ 必须按高风险呈现。

    低估风险的后果：用户在"低风险"标签下同意了一个跨进程执行能力。
    """
    from omnicrawler.gui.views.plugin_market_logic import _permission_risk

    level, _label = _permission_risk({"permissions": ["component:ocr"]})
    assert level == "high", f"component:ocr 被判成 {level}，应至少为 high"


def test_plugin_without_permission_is_rejected() -> None:
    """未声明 `component:ocr` 的插件调不动该能力（fail-closed）。"""
    from omnicrawler.plugins.plugin_broker import CapabilityBroker
    from omnicrawler.plugins.plugin_broker_contracts import CapabilityError

    broker = CapabilityBroker(permissions=set(), system_info={"version": "test"})
    with pytest.raises(CapabilityError) as excinfo:
        broker.dispatch("component.ocr.recognize", {"engine": "tesseract", "component": "x"})
    assert "component:ocr" in str(excinfo.value)


def test_host_revalidates_png_input() -> None:
    """宿主侧必须**再验一次** PNG，不信任插件侧的自我约束。

    边界检查只有一处就是没有检查——插件同样受沙箱限制，
    但输入校验属于协议契约，不该依赖对方守规矩。
    """
    from omnicrawler.plugins.plugin_broker import CapabilityBroker
    from omnicrawler.plugins.plugin_broker_contracts import CapabilityError

    broker = CapabilityBroker(
        permissions={"component:ocr"}, system_info={"version": "test"}
    )

    # 非 base64
    with pytest.raises(CapabilityError):
        broker.dispatch("component.ocr.recognize", {
            "engine": "tesseract", "component": "c", "png_base64": "not-base64!!"
        })

    # 是合法 base64 但不是 PNG
    not_png = base64.b64encode(b"GIF89a-not-a-png").decode()
    with pytest.raises(CapabilityError) as excinfo:
        broker.dispatch("component.ocr.recognize", {
            "engine": "tesseract", "component": "c", "png_base64": not_png
        })
    assert "PNG" in str(excinfo.value)

    # 缺参数
    with pytest.raises(CapabilityError):
        broker.dispatch("component.ocr.recognize", {"engine": "tesseract"})


def test_plugins_domain_does_not_import_pdfx_directly() -> None:
    """支柱②不得直接 import `pdfx`，必须经 facade。"""
    offenders: list[str] = []
    for path in (_SRC / "plugins").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "from ..pdfx" in stripped or "import pdfx" in stripped:
                offenders.append(f"{path.relative_to(_SRC).as_posix()}:{lineno}")
    assert offenders == [], (
        "plugins 域直接依赖了 pdfx：\n  " + "\n  ".join(offenders) +
        "\nPDF 工作台迁成插件后，plugins 不该硬依赖它。请经由 services.document_facade。"
    )


def test_facade_exposes_backend_class() -> None:
    """facade 必须提供后端类入口（plugins 侧的唯一通道）。"""
    from omnicrawler.services import document_facade

    assert hasattr(document_facade, "ocr_backend_class")
    # 返回的是**类**而不是实例：调用方要按每次请求的配置构造后端
    backend_class = document_facade.ocr_backend_class()
    assert inspect.isclass(backend_class)
