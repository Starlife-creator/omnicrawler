"""初始化命令：从模板生成新项目配置。"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

import yaml

# B09-002：name 参与文件路径构造，必须为纯文件名（拒绝路径分隔符与穿越段）。
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

# B3：legacy 平面模板退役后的名字别名 —— 旧名继续可用，落到等价的新结构模板
# （2026-09-23 删除根目录 20 个 legacy 模板；保留 CLI 兼容面，旧脚本/文档里
# 的 --template static_html 不至于 FileNotFoundError）。别名值按**文件相对路径**
# 书写（init 按 _find_template 的文件名解析，不是元数据 id）。
# 无等价新模板的旧名（url_list / httpx_async / incremental / focused）不别名，
# 继续走 examples/configs 的第二级查找；打包环境缺失时报错并列出可用模板。
_TEMPLATE_ALIASES: dict[str, str] = {
    "static_html": "generic/single_page",
    "rest_api": "protocols/rest_offset",
    "graphql": "protocols/graphql",
    "feed": "protocols/feed",
    "sitemap": "protocols/sitemap",
    "sse": "protocols/sse",
    "websocket": "protocols/websocket",
    "long_poll": "protocols/long_poll",
    "browser": "generic/spa_api_discovery",
    "media": "generic/media_gallery",
    "table": "generic/html_table",
    "crawl_bfs": "generic/list_detail",
    "crawl_dfs": "generic/list_detail",
    "authenticated": "authenticated/form_login",
    "form": "generic/search_form",
    "pdf_end_to_end": "documents/pdf_collection",
}


def execute(template: str, output: str, name: str) -> dict[str, Any]:
    requested = template
    template = _TEMPLATE_ALIASES.get(template, template)
    # E4：parents[2] 指向 src/，导致 examples 目录找不到；仓库根才是 parents[3]。
    # 打包环境无 examples 时自动回退内置模板（_find_template 返回 None 后走 bundled）。
    root = Path(__file__).resolve().parents[3]
    bundled = Path(__file__).resolve().parent.parent / "templates"
    # 支持子目录模板，如 generic/single-page、industries/news_articles
    source = _find_template(bundled, template)
    examples_dir = root / "examples" / "configs"
    if not source:
        source = _find_template(examples_dir, template)
    if not source:
        available = _list_templates(bundled)
        raise FileNotFoundError(
            f"模板不存在: {template}\n"
            f"可用模板（内置 {available['count']} 套）:\n{_format_template_list(available)}"
        )
    target_dir = Path(output).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    # B09-002：name 必须为纯文件名（拒绝 / \ .. 与路径穿越）；写盘前断言落点仍在 target_dir 内。
    if not _NAME_RE.fullmatch(name):
        raise ValueError(
            f"项目名非法（仅允许字母数字 _ . -，且不以 . 或 - 开头，最长 64 字符）: {name!r}"
        )
    target = (target_dir / f"{name}.yaml").resolve()
    if target.parent != target_dir:
        raise ValueError(f"目标路径越出输出目录: {target}")
    if target.exists():
        raise FileExistsError(f"目标已存在，不会覆盖: {target}")
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    data.setdefault("project", {})["name"] = name
    data["project"]["workspace"] = f"work/{name}"
    target.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    # pdf_end_to_end 特判按**用户原始请求名**判定（别名改向后仍要带上 bundled/pdf 资产，
    # documents/pdf_collection 的 processors 引用 builtin:pdf/generic_template.yaml）。
    if requested == "pdf_end_to_end" or requested.endswith("pdf_end_to_end"):
        bundled_pdf = bundled / "pdf"
        target_pdf = target_dir / "pdf"
        target_pdf.mkdir(parents=True, exist_ok=True)
        for item in bundled_pdf.iterdir():
            destination = target_pdf / item.name
            if item.is_file() and not destination.exists():
                shutil.copy2(item, destination)
    return {"created": str(target), "next": f"omnicrawler validate -c {target}"}


def _find_template(base_dir: Path, template: str) -> Path | None:
    """在 base_dir 中查找模板文件，支持子目录路径。"""
    # 先尝试精确路径
    for ext in (".yaml", ".yml"):
        candidate = base_dir / f"{template}{ext}"
        if candidate.is_file():
            return candidate
    return None


def _list_templates(base_dir: Path) -> dict[str, Any]:
    """递归列出 base_dir 下所有模板，按类别分组。"""
    categories: dict[str, list[str]] = {}
    count = 0
    for yaml_file in sorted(base_dir.rglob("*.yaml")):
        rel = yaml_file.relative_to(base_dir)
        # 只取一级子目录作为类别
        parts = rel.parts
        cat = parts[0] if len(parts) > 1 else "根目录"
        name = rel.stem
        categories.setdefault(cat, []).append(name)
        count += 1
    return {"categories": categories, "count": count}


def _format_template_list(available: dict[str, Any]) -> str:
    """将模板清单格式化为可读文本。"""
    lines: list[str] = []
    for cat, names in available.get("categories", {}).items():
        lines.append(f"  [{cat}] {', '.join(names)}")
    return "\n".join(lines)
