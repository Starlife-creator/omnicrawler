"""初始化命令：从模板生成新项目配置。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml


def execute(template: str, output: str, name: str) -> dict[str, Any]:
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
    target = target_dir / f"{name}.yaml"
    if target.exists():
        raise FileExistsError(f"目标已存在，不会覆盖: {target}")
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    data.setdefault("project", {})["name"] = name
    data["project"]["workspace"] = f"work/{name}"
    target.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    if template == "pdf_end_to_end" or template.endswith("pdf_end_to_end"):
        bundled_pdf = bundled / "pdf"
        target_pdf = target_dir / "pdf"
        target_pdf.mkdir(parents=True, exist_ok=True)
        for item in bundled_pdf.iterdir():
            destination = target_pdf / item.name
            if item.is_file() and not destination.exists():
                shutil.copy2(item, destination)
    return {"created": str(target), "next": f"omnicrawl validate -c {target}"}


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
