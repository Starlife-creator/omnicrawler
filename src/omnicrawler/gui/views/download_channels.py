"""四类下载通道的统一描述：市场索引 / PyPI 轮子 / 浏览器 CDN / 模型上游。

## 为什么需要这一层

四条通道的机制各不相同，但用户看到的应该是一个一致的"源与镜像设置"界面：

| 通道 | 底层机制 | 已有能力 |
|---|---|---|
| ① 市场索引与自更新 | Ed25519 验签 + `sequence` 防重放 + 墓碑 | `plugin_catalogs` + `aggregate_catalogs` |
| ② Python 轮子镜像 | `MirrorRegistry` 主机级路由 + 强制 sha256 | `mirror_presets`（清华/阿里/中科大/腾讯/华为） |
| ③ 浏览器内核 CDN | `EgressBroker` 白名单 + recipe 固定 sha256 | **此前无镜像配置** |
| ④ OCR/AI 模型上游 | 上游直拉（不伪造哈希） | `model_download`（AIStudio/ModelScope/BOS/HuggingFace） |

本模块把这些差异**声明化**，让 GUI 只负责渲染，不各写一遍。

## 一个必须讲清的区别

四条通道里，只有 ②③ 的构件带**固定 sha256**（镜像返回篡改字节会被拒）；
① 用冷钥签名；④ 的上游**不提供内容哈希**，只能声明信任锚点归属。

这个差异必须对用户诚实呈现——不能笼统说"都安全"。

## 官方站 vs 镜像站

三条差异（写进界面说明卡）：

1. **防篡改能力完全相同**：都有签名或固定哈希，镜像返回篡改字节会被本地拒绝；
2. **同步时效不同**：镜像有同步周期，可能缺最新版本或晚收到下架墓碑
   ⇒ **官方源健康时默认优先官方**；
3. **网络元数据可见性不同**：连镜像站时请求方 IP 与下载时间对镜像运营方可见。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class MirrorEndpoint:
    """一个镜像端点。"""

    label: str
    host: str
    #: 该端点是否有"官方"地位（官方源优先时用它判断）
    official: bool = False


@dataclass(frozen=True, slots=True)
class DownloadChannel:
    """一条下载通道。"""

    channel_id: str
    title: str
    description: str
    #: 官方端点（健康时优先）
    official: list[MirrorEndpoint] = field(default_factory=list)
    #: 内置镜像端点（大厂/高校）
    mirrors: list[MirrorEndpoint] = field(default_factory=list)
    #: 完整性校验机制说明（必须如实描述）
    integrity: str = ""
    #: 用户是否可自定义追加端点
    user_configurable: bool = True
    #: 由哪个配置键控制
    config_key: str = ""


_MIRROR_LABELS: dict[str, str] = {
    "mirrors.tuna.tsinghua.edu.cn": "清华 TUNA",
    "mirrors.aliyun.com": "阿里云",
    "mirrors.cloud.tencent.com": "腾讯云",
    "pypi.mirrors.ustc.edu.cn": "中科大 USTC",
    "repo.huaweicloud.com": "华为云",
    "mirrors.huaweicloud.com": "华为云",
    "registry.npmmirror.com": "淘宝 npmmirror",
}


def _friendly_label(host: str) -> str:
    """主机名 → 用户可读的镜像名（未收录的退回主机名本身）。"""
    return _MIRROR_LABELS.get(host, host)


def _pypi_mirrors() -> list[MirrorEndpoint]:
    """从 `mirror_presets` 取内置 PyPI 镜像（单一真源，不重复维护名单）。"""
    try:
        from ...services.mirror_presets import preset_mirror_group

        groups = preset_mirror_group()
        items = groups.get("pypi.org") or []
        out: list[MirrorEndpoint] = []
        for item in items:
            host = str(item.get("host") or "").strip()
            if not host or host == "pypi.org":
                continue
            out.append(MirrorEndpoint(_friendly_label(host), host))
        return out
    except Exception:  # noqa: BLE001 - 预设不可用时返回空，界面只少几个快捷项
        return []


def _model_sources() -> list[MirrorEndpoint]:
    """从 `model_download` 取上游模型源（同样不重复维护名单）。"""
    try:
        from ...services.model_download import MODEL_SOURCES, SOURCE_HINTS

        return [
            MirrorEndpoint(
                f"{SOURCE_HINTS.get(name, name)}（{name}）",
                name,
                official=index == 0,  # 默认源视为官方首选
            )
            for index, name in enumerate(MODEL_SOURCES)
        ]
    except Exception:  # noqa: BLE001
        return []


#: 四类通道的静态描述。**这里是唯一真源**——GUI、CLI、文档都从这里读。
CHANNELS: tuple[DownloadChannel, ...] = (
    DownloadChannel(
        channel_id="market",
        title="市场索引与自更新",
        description=(
            "插件 / 模板 / 组件的 catalog.json 与宿主自更新 latest.json。"
            "支持多个静态目录树源（官方 Pages / Releases + 自建 HTTPS 镜像或局域网目录）。"
        ),
        official=[MirrorEndpoint("官方 GitHub Pages / Releases", "github.com", official=True)],
        mirrors=[],
        integrity="Ed25519 冷钥签名 + sequence 单调递增防重放 + tombstones 吊销拦截",
        config_key="plugins.catalogs",
    ),
    DownloadChannel(
        channel_id="pypi",
        title="Python 轮子与组件构件",
        description="Wheel-Pack 组件（如 ocr-paddle）的 .whl 闭包及受控依赖。",
        official=[
            MirrorEndpoint("PyPI 官方", "pypi.org", official=True),
            MirrorEndpoint("PyPI 文件存储", "files.pythonhosted.org", official=True),
        ],
        mirrors=_pypi_mirrors(),
        integrity="每个 .whl 强制核对 recipe.json 里锁定的 64 位 SHA-256",
        config_key="mirrors.groups",
    ),
    DownloadChannel(
        channel_id="browser",
        title="浏览器内核与驱动",
        description=(
            "按需加装的 Firefox / WebKit / Chromium 更新包及驱动。"
            "随包预置的 Chromium 不受此通道影响（它已在本机，无需下载）。"
        ),
        official=[
            MirrorEndpoint("Playwright CDN", "playwright.azureedge.net", official=True),
            MirrorEndpoint("Playwright CDN（新版）", "cdn.playwright.dev", official=True),
        ],
        mirrors=[
            MirrorEndpoint("淘宝 npmmirror", "registry.npmmirror.com"),
            MirrorEndpoint("华为云镜像", "mirrors.huaweicloud.com"),
        ],
        integrity="EgressBroker 白名单受控下载 + recipe 固定构件 SHA-256",
        config_key="browser.download_host",
    ),
    DownloadChannel(
        channel_id="models",
        title="OCR / AI 模型上游",
        description=(
            "PaddleOCR 等大模型权重（1 GB 级）。"
            "**本项目不托管也不伪造模型哈希**——完整性由上游负责。"
        ),
        official=_model_sources(),
        mirrors=[],
        integrity=(
            "上游不提供内容哈希；本项目的立场是「内容完整性由上游负责，"
            "本项目不为其背书」，仅在界面上标注信任锚点归属"
        ),
        # 模型源是"选择哪个上游"，不是"镜像同一个东西"
        user_configurable=True,
        config_key="ocr.model_source",
    ),
)


def channel(channel_id: str) -> DownloadChannel | None:
    """按 id 取通道描述。"""
    for item in CHANNELS:
        if item.channel_id == channel_id:
            return item
    return None


def channel_ids() -> list[str]:
    return [item.channel_id for item in CHANNELS]


#: 官方站 vs 镜像站的差异说明（界面说明卡正文，逐条可核对）。
OFFICIAL_VS_MIRROR_NOTES: tuple[tuple[str, str], ...] = (
    (
        "防篡改能力：完全相同",
        "市场条目有 Ed25519 冷钥签名、轮子与浏览器构件有固定 SHA-256。"
        "镜像即使返回被篡改的字节，也会被本地校验直接拒绝——"
        "换镜像不降低防投毒安全性。",
    ),
    (
        "同步时效：镜像可能滞后",
        "镜像站按周期同步（数分钟至数小时），可能拉不到刚发布的版本，"
        "或晚一步收到插件紧急下架（tombstones）。"
        "因此官方源连通正常时，系统默认优先请求官方源。",
    ),
    (
        "网络元数据可见性：不同",
        "连接镜像站时，请求方公网 IP 与下载时间对镜像运营机构（高校或云厂商）可见；"
        "上游模型源（AIStudio / ModelScope / HuggingFace）由对应平台直接提供内容。",
    ),
)

#: 切换提醒的触发边界（给界面用的说明文本）。
MIRROR_PROMPT_BOUNDARY = (
    "仅在官方源发生网络连接失败、DNS 解析失败、TLS 握手超时或持续低速时提示切换镜像。"
    "签名校验失败、哈希不符、sequence 回退、ABI/平台不兼容一律阻断报错——"
    "绝不诱导用户通过切换镜像绕过校验。"
)


def describe_channels() -> list[dict[str, Any]]:
    """把四类通道渲染成 GUI 可直接消费的结构。"""
    return [
        {
            "id": item.channel_id,
            "title": item.title,
            "description": item.description,
            "integrity": item.integrity,
            "config_key": item.config_key,
            "user_configurable": item.user_configurable,
            "official": [
                {"label": e.label, "host": e.host, "official": e.official}
                for e in item.official
            ],
            "mirrors": [{"label": e.label, "host": e.host} for e in item.mirrors],
        }
        for item in CHANNELS
    ]
