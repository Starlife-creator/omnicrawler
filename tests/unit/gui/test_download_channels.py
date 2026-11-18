"""四类下载通道的声明化描述：界面与文档的唯一真源。

## 为什么需要

「源与下载镜像设置」要统一四条机制完全不同的通道：

| 通道 | 完整性机制 |
|---|---|
| 市场索引 | Ed25519 签名 + sequence 防重放 + 墓碑 |
| Python 轮子 | 强制 sha256（recipe 锁定） |
| 浏览器 CDN | EgressBroker 白名单 + 固定 sha256 |
| 模型上游 | **上游不提供哈希**，只声明信任锚点归属 |

如果各界面各写一遍这些说明，很快就会出现"某处说都安全、另一处说模型不校验"
的矛盾。所以集中成一份声明，界面只渲染。

## 判据（关键的一条是诚实性）

1. 四条通道齐全，每条都要有 `integrity` 说明；
2. **模型通道必须如实写明"上游不提供哈希"**——不能伪装成有校验；
3. 镜像名单来自既有真源（`mirror_presets` / `model_download`），不重复维护；
4. 官方站 vs 镜像站的三条差异必须齐备（防篡改 / 时效 / 可见性）；
5. 切换提醒的边界必须写明"安全失败绝不提示换源"。
"""

from __future__ import annotations

import pytest

from omnicrawler.gui.views.download_channels import (
    CHANNELS,
    MIRROR_PROMPT_BOUNDARY,
    OFFICIAL_VS_MIRROR_NOTES,
    channel,
    channel_ids,
    describe_channels,
)


def test_four_channels_exist() -> None:
    """四类通道齐全：市场 / 轮子 / 浏览器 / 模型。"""
    assert set(channel_ids()) == {"market", "pypi", "browser", "models"}


def test_every_channel_has_integrity_statement() -> None:
    """每条通道都必须说明完整性机制——用户有权知道"这个源靠什么保证真实"。"""
    for item in CHANNELS:
        assert item.integrity.strip(), f"通道 {item.channel_id} 没写完整性机制"


def test_every_channel_has_official_or_mirror() -> None:
    """每条通道至少要有一个可用端点。"""
    for item in CHANNELS:
        assert item.official or item.mirrors, (
            f"通道 {item.channel_id} 一个端点都没有，界面会是空的"
        )


def test_model_channel_is_honest_about_missing_hash() -> None:
    """模型通道必须如实写明上游不提供内容哈希。

    这是诚实性问题：不能把它伪装成"和别的通道一样有校验"。
    `asset_trust.py` 的立场是「内容完整性由上游负责，本项目不为其背书」。
    """
    models = channel("models")
    assert models is not None
    text = models.integrity
    assert "不提供" in text or "不伪造" in text, (
        f"模型通道的完整性说明未交代上游不给哈希：{text!r}"
    )
    assert "背书" in text or "上游负责" in text, (
        f"模型通道未声明信任锚点归属上游：{text!r}"
    )


def test_pypi_mirrors_come_from_existing_source() -> None:
    """PyPI 镜像必须来自 `mirror_presets`，不重复维护名单。"""
    pytest.importorskip("omnicrawler.services.mirror_presets")
    pypi = channel("pypi")
    assert pypi is not None
    assert pypi.mirrors, "PyPI 通道没有镜像 —— 与 mirror_presets 的对接失效"

    hosts = {m.host for m in pypi.mirrors}
    # 至少要有清华/阿里这类常见镜像
    assert any("tuna" in h or "aliyun" in h for h in hosts), (
        f"内置镜像名单异常：{sorted(hosts)}"
    )
    # 官方源不能被当成镜像
    assert "pypi.org" not in hosts


def test_model_sources_come_from_existing_source() -> None:
    """模型上游源必须来自 `model_download`，且默认源排第一。"""
    pytest.importorskip("omnicrawler.services.model_download")
    models = channel("models")
    assert models is not None
    assert models.official, "模型通道没有上游源"

    labels = " ".join(e.host for e in models.official)
    for name in ("aistudio", "huggingface", "modelscope", "bos"):
        assert name in labels, f"模型通道缺上游源 {name}"

    # 默认源（aistudio）应被标为官方首选
    assert models.official[0].official is True


def test_browser_channel_has_mirrors() -> None:
    """浏览器通道此前无镜像配置，现在必须有。"""
    browser = channel("browser")
    assert browser is not None
    assert browser.mirrors, "浏览器 CDN 通道没有镜像"
    hosts = {m.host for m in browser.mirrors}
    assert any("npmmirror" in h or "huawei" in h for h in hosts), (
        f"浏览器镜像名单异常：{sorted(hosts)}"
    )


def test_official_vs_mirror_notes_cover_three_topics() -> None:
    """差异说明必须覆盖三条：防篡改 / 时效 / 可见性。"""
    text = " ".join(f"{title} {body}" for title, body in OFFICIAL_VS_MIRROR_NOTES)
    assert "防篡改" in text, "缺防篡改说明"
    assert "时效" in text or "滞后" in text, "缺同步时效说明"
    assert "可见" in text or "IP" in text, "缺网络元数据可见性说明"


def test_integrity_note_states_mirror_does_not_weaken_security() -> None:
    """必须明确写出"换镜像不降低防投毒安全性"。"""
    text = " ".join(body for _title, body in OFFICIAL_VS_MIRROR_NOTES)
    assert "不降低" in text or "同样" in text, (
        "未说明镜像与官方在防篡改上等价 —— 用户会以为换镜像更不安全"
    )


def test_prompt_boundary_excludes_security_failures() -> None:
    """切换提醒的边界必须写明：安全类失败绝不提示换源。"""
    text = MIRROR_PROMPT_BOUNDARY
    for keyword in ("签名", "哈希", "sequence", "ABI"):
        assert keyword in text, f"边界说明未提及 {keyword} 类失败"
    assert "绝不" in text or "不得" in text, (
        "未强调「绝不诱导用户绕过校验」这一条"
    )


def test_describe_channels_is_json_safe() -> None:
    """`describe_channels()` 的输出必须可直接给 GUI / JSON 用。"""
    import json

    payload = describe_channels()
    assert len(payload) == 4
    # 能序列化即证明没有混入非 JSON 类型
    json.dumps(payload)
    for item in payload:
        for key in ("id", "title", "description", "integrity", "official", "mirrors"):
            assert key in item, f"通道 {item.get('id')} 缺 {key}"


def test_channel_lookup_by_unknown_id_returns_none() -> None:
    assert channel("nope") is None
