"""组件 recipe 安装的宿主入口：CLI + 下载器接线。

## 为什么需要这层

`services/component_recipe.py` 已经把装配引擎写完整了（哈希校验、平台/ABI
兼容性、两阶段落盘、zip-slip 防护），但它的 `install_from_recipe()` 要求
**调用方注入 downloader**——设计上刻意不内置网络出口。

实测：该函数此前**只有单元测试在用**，没有任何生产入口。本模块把它接上：

```
CLI:  omnicrawler components install-recipe <recipe.json>
       └─ 用 market_client 的受控出站下载
```

## 为什么下载器必须走 `market_client._read`

`_read` 已经处理了三件本模块不该重复实现的事：

1. **fail-closed**：`egress=None` 时直接 `PolicyBlockedError`，不裸 urlopen；
2. **审计与预算**：`egress.request(..., purpose="plugin")` 过同一条出口；
3. **重定向安全**：`SafeRedirectHandler` 不跟随任意跳转。

自己写一个 `urllib.urlopen` 是**绕过安全边界**，不做。

## 与整包 zip 的关系

两种组件安装方式并存，语义不同：

| 方式 | 字节来源 | 大小 | 用途 |
|---|---|---|---|
| 整包 zip（`ComponentManager.install`） | 用户手工下载的本地文件 | 大（含二进制） | 离线/内网分发 |
| recipe（本模块） | **上游 URL**，本地组装 | **小**（只有几十 KB 声明） | 市场分发 |

市场只托管 recipe——这正是维护者 2026-11-18 确认的分工：
「**下载从上游拉，需要组装就在本地组装，本项目只维护组装方式。**」
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core.errors import PolicyBlockedError
from .component_recipe import RecipeError, install_from_recipe, load_recipe, recipe_digest


class RecipeInstallError(RuntimeError):
    """recipe 安装失败（下载、校验或装配任一阶段）。"""


def default_downloader(egress: Any = None):
    """返回一个 ``(url, dest) -> None`` 下载器，走受控出站。

    :param egress: 出口策略。**必须显式提供**；为 None 时
        `market_client._read` 会 fail-closed 抛 `PolicyBlockedError`。
        这不是缺陷而是设计——把它透传上来，让调用方看到真实原因。
    """

    def _download(url: str, destination: Path) -> None:
        from ..plugins.market_client import _read

        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            payload = _read(url, egress=egress)
        except PolicyBlockedError as exc:
            raise RecipeInstallError(
                f"组件下载被出口策略阻止：{exc}（提示：{getattr(exc, 'suggestion', '')}）"
            ) from exc
        destination.write_bytes(payload)

    return _download


def install_recipe_file(
    recipe_path: str | Path,
    component_root: str | Path,
    *,
    egress: Any = None,
    downloader: Any = None,
    work_dir: str | Path | None = None,
) -> dict[str, Any]:
    """从 recipe 文件安装组件，返回装配摘要。

    与 `component_recipe.install_from_recipe` 的区别：本函数负责
    **文件加载 + 下载器接线 + 错误翻译**，让 CLI/GUI 只调一处。

    摘要里额外带 `recipe_digest`——市场侧可用它做"装的是不是审查过的那份 recipe"。
    """
    path = Path(recipe_path)
    if not path.is_file():
        raise RecipeInstallError(f"recipe 文件不存在：{path}")

    try:
        recipe = load_recipe(path)
    except RecipeError as exc:
        raise RecipeInstallError(f"recipe 校验失败：{exc}") from exc

    digest = recipe_digest(recipe)

    # **必须先把安装根建出来**：`install_from_recipe` 用
    # `tempfile.TemporaryDirectory(dir=component_root)` 做暂存区，
    # 目录不存在会抛 FileNotFoundError，看起来像"哈希校验失败"的假象
    # （实测踩到：错误信息指向 `.recipe-xxxx` 而不是哈希不符）。
    root = Path(component_root)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RecipeInstallError(f"无法创建组件安装根目录 {root}：{exc}") from exc

    engine = downloader if downloader is not None else default_downloader(egress)

    try:
        summary = install_from_recipe(
            recipe,
            root,
            downloader=engine,
            work_dir=Path(work_dir) if work_dir else None,
        )
    except RecipeError as exc:
        raise RecipeInstallError(f"组件装配失败：{exc}") from exc

    result = dict(summary) if isinstance(summary, dict) else {"summary": summary}
    # 引擎已返回 recipe_digest；仅在缺失时补算，避免覆盖它算出的值。
    result.setdefault("recipe_digest", digest)
    result["recipe_file"] = str(path)
    return result


#: 压缩比估算：`.whl` 是 ZIP，纯 Python 包几乎不压缩，原生扩展约 2×。
#:
#: 这是**估算**而不是实测：实测要解压全部构件（几百 MB），不该在
#: `inspect-recipe`（只读、秒级）里做。宁可给一个偏保守的数并标注是估算，
#: 也不要让用户以为「下载 322 MB 就只占 322 MB」。
_COMPRESSION_RATIO = 2.0


def estimate_installed_bytes(recipe: dict[str, Any], download_bytes: int) -> int:
    """估算解压后的磁盘占用。

    判据：纯数据（`format: raw`）几乎不压缩，按 1× 算；
    wheel 按 :data:`_COMPRESSION_RATIO` 算。取整到 MiB 便于展示。
    """
    raw_bytes = 0
    for artifact in recipe.get("artifacts") or []:
        if str(artifact.get("format") or "").lower() == "raw":
            raw_bytes += int(artifact.get("size") or 0)
    wheel_bytes = max(0, download_bytes - raw_bytes)
    estimated = raw_bytes + int(wheel_bytes * _COMPRESSION_RATIO)
    # 取整到 MiB
    mib = 1024 * 1024
    return (estimated // mib) * mib


def format_bytes(value: int) -> str:
    """人类可读的字节数（MiB / GiB）。"""
    mib = 1024 * 1024
    if value >= 1024 * mib:
        return f"{value / (1024 * mib):.1f} GiB"
    return f"{value / mib:.0f} MiB"


def describe_recipe(recipe_path: str | Path) -> dict[str, Any]:
    """只读地描述一份 recipe（不下载、不落盘）。

    用于 `omnicrawler components inspect-recipe`——让用户在**下载之前**
    看到要拉哪些 URL、多大、什么哈希。
    """
    path = Path(recipe_path)
    if not path.is_file():
        raise RecipeInstallError(f"recipe 文件不存在：{path}")
    try:
        recipe = load_recipe(path)
    except RecipeError as exc:
        raise RecipeInstallError(f"recipe 校验失败：{exc}") from exc

    # 与 `install_from_recipe` 用**同一个**平台筛选：只列出本平台真要拉的构件。
    # 否则用户在 `inspect-recipe` 看到 3 个 URL、实际只拉 1 个，会以为漏装。
    from .component_recipe import artifacts_for_platform, current_platform

    artifacts = [
        {
            "url": item["url"],
            "sha256": item["sha256"],
            "size": item.get("size"),
            "extract_to": item.get("extract_to", ""),
        }
        for item in artifacts_for_platform(recipe, current_platform())
    ]
    total = sum(int(a["size"] or 0) for a in artifacts)
    return {
        "name": recipe["name"],
        "version": recipe["version"],
        "description": recipe.get("description", ""),
        "platforms": recipe.get("platforms", []),
        "architectures": recipe.get("architectures", []),
        "entry": recipe.get("entry", {}),
        "artifacts": artifacts,
        # **下载量**（wheel 压缩后）。与下面的磁盘占用是两个不同的数。
        "declared_total_bytes": total,
        # **磁盘占用估算**（解压后）：`.whl` 是 ZIP，原生扩展压缩比约 2×。
        # 分开报是因为只给下载量会形成虚假安全感 ——
        # `ocr-paddle` 在 Linux 上下载 322 MB，解压后约 600–700 MB。
        "estimated_installed_bytes": estimate_installed_bytes(recipe, total),
        "recipe_digest": recipe_digest(recipe),
    }


def dumps_summary(summary: dict[str, Any]) -> str:
    """把装配摘要渲染成 CLI 文本。"""
    lines = [
        f"组件：{summary.get('name', '?')} {summary.get('version', '?')}",
        f"安装到：{summary.get('path', '?')}",
        f"入口：{summary.get('entry', '?')}",
        f"recipe 摘要：{summary.get('recipe_digest', '?')[:16]}…",
    ]
    return "\n".join(lines)


def summary_json(summary: dict[str, Any]) -> str:
    return json.dumps(summary, ensure_ascii=False, indent=2)

def components_size_summary(component_root: Path) -> dict[str, int | str]:
    """已装组件的**总占用**（按磁盘上的实际字节数统计）。

    维护者的要求是「不设上限，但要让用户看到总共占多少」。
    这里统计的是**真实磁盘占用**（不是下载量估算）—— 已装的东西可以直接量。
    """
    root = Path(component_root)
    total = 0
    count = 0
    if root.is_dir():
        for version_dir in root.iterdir():
            if not version_dir.is_dir():
                continue
            count += 1
            for path in version_dir.rglob("*"):
                try:
                    if path.is_file():
                        total += path.stat().st_size
                except OSError:
                    continue
    return {
        "components": count,
        "installed_bytes": total,
        "human": format_bytes(total),
    }
