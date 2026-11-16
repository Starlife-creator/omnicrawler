"""组件验签失败必须给出**可读**原因（不是空字符串）。

**真实缺陷**

`ComponentManager.inspect_package` 在签名不匹配时直接透传
`cryptography.exceptions.InvalidSignature`。该异常的 `str()` 是**空串**，
于是用户看到的是一个没有任何信息的失败——不知道是"签名不对"还是
"文件缺了"，也无从判断下一步做什么。

仓库在 `services/update_feed.py` 里已经处理过同一类问题（那里的注释写着
「InvalidSignature 的 str() 是空串 —— 直接透传会得到『验签失败: 』这种
没信息量的告警」），但组件这条路径**没有**做同样处理。

**判据**

1. 签名不匹配 ⇒ 抛出**非空**、能说明"签名不对"的错误；
2. 缺失签名字段 ⇒ 明确说"缺少签名"，而不是笼统的验签失败；
3. 受信公钥未配置 ⇒ 明确提示"缺少信任根"（这是配置问题，不是包的问题）；
4. 三种原因**互不混淆**——用户据此就能判断该找谁（改包 / 补配置）。
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest


def _package(tmp_path: Path, *, signature: str = "", name: str = "demo") -> Path:
    """造一个最小组件包（只含 component.json，不含实际文件）。"""
    manifest: dict[str, object] = {"name": name, "version": "1.0.0", "files": {}}
    if signature:
        manifest["signature"] = signature
    package = tmp_path / f"{name}.ocp"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("component.json", json.dumps(manifest))
    return package


@pytest.fixture()
def manager_with_trust(tmp_path: Path):
    """用真实的第三把钥匙（随包 `configs/component_trust.pub.pem`）构造管理器。"""
    from omnicrawler.services.component_manager import ComponentManager
    from omnicrawler.services.component_trust import load_trusted_key

    key = load_trusted_key()
    assert key is not None, "configs/component_trust.pub.pem 缺失，无法验证验签路径"
    return ComponentManager(tmp_path / "components", trusted_public_key=key)


def test_bad_signature_error_is_not_empty(tmp_path: Path, manager_with_trust) -> None:
    """签名不匹配时错误消息必须非空且有信息量。"""
    package = _package(tmp_path, signature="AAAA")  # 非法 base64/长度不对的签名

    with pytest.raises(Exception) as excinfo:
        manager_with_trust.inspect_package(package)

    message = str(excinfo.value)
    assert message.strip(), (
        "验签失败但没有错误消息——用户看到一个空白提示，"
        "无法判断是签名不对、文件缺失还是配置问题"
    )
    lowered = message.lower()
    assert ("签名" in message) or ("signature" in lowered), (
        f"错误消息没有说明是签名问题：{message!r}"
    )


def test_missing_signature_says_so(tmp_path: Path, manager_with_trust) -> None:
    """包里没有 signature 字段时，应明确说"缺少签名"。"""
    package = _package(tmp_path, signature="")

    with pytest.raises(Exception) as excinfo:
        manager_with_trust.inspect_package(package)

    message = str(excinfo.value)
    assert message.strip(), "缺签名但错误消息为空"
    assert ("签名" in message) or ("signature" in message.lower())


def test_missing_trust_root_is_distinguishable(tmp_path: Path) -> None:
    """未配置信任根 ⇒ 错误要指向配置问题，而不是笼统的验签失败。"""
    from omnicrawler.services.component_manager import ComponentManager

    manager = ComponentManager(tmp_path / "components", trusted_public_key=None)
    package = _package(tmp_path, signature="AAAA")

    with pytest.raises(Exception) as excinfo:
        manager.inspect_package(package)

    message = str(excinfo.value)
    assert message.strip(), "缺信任根但错误消息为空"
    assert ("受信" in message) or ("信任" in message) or ("trust" in message.lower()), (
        f"没有提示是信任根缺失（配置问题）：{message!r}"
    )


def test_three_failure_reasons_are_distinct(tmp_path: Path, manager_with_trust) -> None:
    """三种失败原因的**消息必须互不相同**——否则用户无法据此定位。"""
    from omnicrawler.services.component_manager import ComponentManager

    messages: dict[str, str] = {}

    for label, manager, pkg in (
        ("bad_sig", manager_with_trust, _package(tmp_path, signature="AAAA", name="a")),
        ("no_sig", manager_with_trust, _package(tmp_path, signature="", name="b")),
    ):
        with pytest.raises(Exception) as excinfo:
            manager.inspect_package(pkg)
        messages[label] = str(excinfo.value)

    no_trust = ComponentManager(tmp_path / "c2", trusted_public_key=None)
    with pytest.raises(Exception) as excinfo:
        no_trust.inspect_package(_package(tmp_path, signature="AAAA", name="c"))
    messages["no_trust"] = str(excinfo.value)

    assert len(set(messages.values())) == 3, (
        f"三种失败原因的消息有重复，用户无法区分：{messages}"
    )
