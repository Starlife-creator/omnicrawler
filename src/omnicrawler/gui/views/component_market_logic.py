"""组件市场的纯逻辑层（无 Qt 依赖，可独立单测）。

## 定位

市场三分支中，插件与模板都有「拉 catalog → 验签 → 浏览 → 安装」的完整链路；
组件此前**只有离线 zip 导入**，没有任何市场能力。本模块补齐前半段：
从已验签的 catalog 里取出 `components` 数组并做展示/风险分级。

与 `template_market.py` / `plugin_market_logic.py` 的分工一致：

| 模块 | 职责 |
|---|---|
| `market_client.fetch_catalog_verified` | 拉取 + 验签 + 防重放（共用） |
| `component_market_logic`（本模块） | 组件条目的解析、分组、风险分级、安装前审查 |
| `component_manager.ComponentsPanel` | Qt 展示与动作触发 |

## 组件与插件/模板的关键差别

组件条目**没有包文件与包哈希**——市场只托管 recipe 声明，字节从上游拉。
所以这里的"风险"不是"这个包可信吗"，而是：

* recipe 指向哪些**上游域名**（用户要能看见"会连哪里"）；
* 声明要下载**多少字节**（几百 MB 的模型要提前知道）；
* 构件是否**都带 sha256**（缺哈希就无法校验来源）。

这些都在**下载之前**告诉用户——装到一半才发现要拉 2 GB 是最糟的体验。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from ..i18n import _

#: 上游域名 → 风险说明。未列出的域名按「未知来源」处理，一律提示确认。
_KNOWN_HOSTS: dict[str, tuple[str, str]] = {
    "github.com": ("low", _("GitHub 发布页")),
    "raw.githubusercontent.com": ("low", _("GitHub 原始文件")),
    "objects.githubusercontent.com": ("low", _("GitHub 对象存储")),
    "huggingface.co": ("medium", _("HuggingFace 模型仓")),
    "cdn-lfs.huggingface.co": ("medium", _("HuggingFace LFS 存储")),
    "modelscope.cn": ("medium", _("ModelScope 模型仓")),
    "paddlepaddle.bj.bcebos.com": ("medium", _("Paddle 官方对象存储")),
    "paddleocr.bj.bcebos.com": ("medium", _("PaddleOCR 官方对象存储")),
}

#: 单次安装的声明大小阈值（字节）。超过则要求用户二次确认。
LARGE_DOWNLOAD_BYTES = 200 * 1024 * 1024  # 200 MB


@dataclass(frozen=True)
class ComponentArtifact:
    """一个上游构件（下载后按 sha256 校验）。"""

    url: str
    sha256: str
    size: int | None
    host: str
    host_risk: str
    host_label: str

    @property
    def has_valid_digest(self) -> bool:
        return len(self.sha256) == 64 and all(c in "0123456789abcdef" for c in self.sha256)


@dataclass
class ComponentEntry:
    """catalog 里的一个组件条目，已解析成便于展示的形态。"""

    id: str
    name: str
    version: str
    publisher: str
    description_file: str
    recipe_file: str
    recipe_digest: str
    declared_total_bytes: int
    artifacts: list[ComponentArtifact] = field(default_factory=list)

    @property
    def hosts(self) -> list[str]:
        seen: list[str] = []
        for artifact in self.artifacts:
            if artifact.host and artifact.host not in seen:
                seen.append(artifact.host)
        return seen

    @property
    def host_risks(self) -> list[str]:
        return sorted({a.host_risk for a in self.artifacts})


def _host_of(url: str) -> tuple[str, str, str]:
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        host = ""
    risk, label = _KNOWN_HOSTS.get(host, ("unknown", _("未知来源")))
    return host, risk, label


def parse_component_entry(entry: dict[str, Any]) -> ComponentEntry | None:
    """把 catalog 条目解析成 `ComponentEntry`；结构非法时返回 None。

    **不抛异常**：一个坏条目不该让整个市场页打不开。调用方应把它
    作为"该条目无法安装"展示，而不是让用户面对一片空白。
    """
    if not isinstance(entry, dict):
        return None
    component_id = str(entry.get("id") or "").strip()
    version = str(entry.get("version") or "").strip()
    if not component_id or not version:
        return None

    artifacts: list[ComponentArtifact] = []
    raw_artifacts = entry.get("artifacts")
    if isinstance(raw_artifacts, list):
        for item in raw_artifacts:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url.startswith(("https://", "http://")):
                continue
            host, risk, label = _host_of(url)
            size = item.get("size")
            artifacts.append(
                ComponentArtifact(
                    url=url,
                    sha256=str(item.get("sha256") or ""),
                    size=int(size) if isinstance(size, int) and size >= 0 else None,
                    host=host,
                    host_risk=risk,
                    host_label=label,
                )
            )

    total = entry.get("declared_total_bytes")
    if not isinstance(total, int):
        total = sum(a.size or 0 for a in artifacts)

    return ComponentEntry(
        id=component_id,
        name=str(entry.get("name") or component_id),
        version=version,
        publisher=str(entry.get("publisher") or "unknown"),
        description_file=str(entry.get("description_file") or ""),
        recipe_file=str(entry.get("recipe_file") or ""),
        recipe_digest=str(entry.get("recipe_digest") or ""),
        declared_total_bytes=total,
        artifacts=artifacts,
    )


def components_from_catalog(catalog: dict[str, Any]) -> list[ComponentEntry]:
    """从已验签的 catalog 取组件条目（按 id 排序）。

    缺 `components` 键时返回空列表——**向后兼容**旧 catalog。
    """
    raw = catalog.get("components")
    if not isinstance(raw, list):
        return []
    parsed = [parse_component_entry(item) for item in raw]
    return sorted(
        (item for item in parsed if item is not None),
        key=lambda item: item.id,
    )


def pre_install_checks(entry: ComponentEntry) -> tuple[list[str], list[str]]:
    """安装前审查，返回 (阻断项, 提示项)。

    **阻断**（不能装）：
    * 没有构件；
    * 任一构件缺合法 sha256（无哈希无法校验来源）；
    * 缺 recipe_digest（无法核对装的是不是审查过的那份声明）。

    **提示**（能装但要告知）：
    * 声明大小超过 200 MB；
    * 上游域名未知或属于中等风险。
    """
    blockers: list[str] = []
    warnings: list[str] = []

    if not entry.artifacts:
        blockers.append(_("该组件没有可下载的构件声明"))
    for artifact in entry.artifacts:
        if not artifact.has_valid_digest:
            blockers.append(
                _("构件缺少合法 sha256：{url}（没有哈希无法校验来源）").format(url=artifact.url)
            )
    if not entry.recipe_digest:
        blockers.append(_("条目缺少 recipe 摘要，无法核对声明是否被改动"))

    if entry.declared_total_bytes > LARGE_DOWNLOAD_BYTES:
        mb = entry.declared_total_bytes / (1024 * 1024)
        warnings.append(_("需要下载 {mb:.0f} MB，请注意流量与磁盘占用").format(mb=mb))
    for artifact in entry.artifacts:
        if artifact.host_risk == "unknown":
            warnings.append(_("上游域名未知：{host} 请确认可信").format(host=artifact.host or _("(无法解析)")))
        elif artifact.host_risk == "medium":
            warnings.append(_("上游为{label}：{host}").format(label=artifact.host_label, host=artifact.host))
    return blockers, warnings


def human_bytes(value: int) -> str:
    """把字节数渲染成便于阅读的形态（CLI/GUI 共用）。"""
    if value <= 0:
        return _("未知")
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value = int(value / 1024)
    return f"{value} B"


def describe_for_confirm(entry: ComponentEntry) -> str:
    """安装确认对话框的正文（纯文本，GUI 与 CLI 共用）。"""
    lines = [
        f"{entry.name} {entry.version}",
        _("发布者：{p}").format(p=entry.publisher),
        _("需要下载：{size}").format(size=human_bytes(entry.declared_total_bytes)),
        _("上游域名：{hosts}").format(hosts=', '.join(entry.hosts) or _("(无)")),
        "",
        _("安装时会从上述上游拉取文件，并按条目声明的 sha256 校验。"),
        _("本项目不托管这些字节，也不为上游内容背书。"),
    ]
    blockers, warnings = pre_install_checks(entry)
    if warnings:
        lines.append("")
        lines.append(_("请注意："))
        lines.extend(f"  · {w}" for w in warnings)
    if blockers:
        lines.append("")
        lines.append(_("无法安装："))
        lines.extend(f"  · {b}" for b in blockers)
    return "\n".join(lines)


def filter_components(entries: list[ComponentEntry], query: str) -> list[ComponentEntry]:
    """按关键词过滤组件条目。

    匹配 **id / name / publisher / 上游域名（hosts）**，两侧都归一大小写。

    与插件页 / 模板页的**共同部分**一致（都 casefold、都是子串匹配）；
    差异在**各自的专有字段** —— 组件有 publisher/hosts，插件有 plugin_types，
    模板两者都没有。

    ★ 2026-11-24 更正：原文写「同口径」，会被读成「字段集相同」。
    实际是「**共通字段的行为一致**」。
    """
    needle = (query or "").strip().casefold()
    if not needle:
        return list(entries)
    matched: list[ComponentEntry] = []
    for entry in entries:
        haystack = " ".join(
            [entry.id, entry.name, entry.publisher, *entry.hosts]
        ).casefold()
        if needle in haystack:
            matched.append(entry)
    return matched
