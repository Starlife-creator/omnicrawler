"""Plugin SDK scaffolding and contract checks for signed, permission-scoped plugins."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path

from .plugin_sandbox import PluginPackageManifest

_ID = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")

__all__ = [
    "contract_check",
    "scaffold_contract2",
    "scaffold_plugin",
    "validate_plugin_id",
]


def validate_plugin_id(plugin_id: str) -> None:
    if not _ID.fullmatch(plugin_id):
        raise ValueError("插件ID必须以小写字母开头，仅含小写字母、数字、下划线或短横线")


def scaffold_plugin(root: Path, manifest: PluginPackageManifest) -> Path:
    """Create a minimal plugin package without installing or executing it."""
    validate_plugin_id(manifest.plugin_id)
    if root.exists() and any(root.iterdir()):
        raise FileExistsError("插件目录必须为空")
    root.mkdir(parents=True, exist_ok=True)
    (root / "plugin.json").write_text(json.dumps(asdict(manifest), ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "plugin.py").write_text(
        '"""Generated OmniCrawler plugin entry."""\n\ndef handle(operation, payload):\n'
        "    return {'operation': operation, 'payload': payload}\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        f"# {manifest.plugin_id}\n\n权限：{', '.join(manifest.permissions) or '无'}\n\n"
        "运行 `pytest` 契约测试后再签名发布；权限变化必须重新获得用户批准。\n",
        encoding="utf-8",
    )
    return root


def contract_check(root: Path) -> tuple[str, ...]:
    errors: list[str] = []
    try:
        manifest = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
        validate_plugin_id(str(manifest.get("plugin_id", "")))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        errors.append(str(exc))
    if not (root / "plugin.py").is_file():
        errors.append("缺少 plugin.py")
    return tuple(errors)


def scaffold_contract2(root: Path, plugin_id: str, *, display_name: str = "", version: str = "0.1.0") -> Path:
    """生成契约 2 工程骨架（Phase 3：plugins scaffold-contract2）。

    方案第 38/67 轮：新建契约 2 工程（而非原地改造成契约 2）——产出
    handle 骨架 + PLUGIN_METADATA/plugin.yaml 双通道字段对齐 + 契约测试入口。
    业务逻辑迁移由作者按 SDK 指引完成；生成后本地 audit + 契约测试验证。
    """
    from datetime import date

    from .plugin_sdk_contract2 import (
        PLUGIN_YAML_TEMPLATE,
        build_plugin_py,
        build_test_conftest,
        validate_contract2_id,
    )

    validate_contract2_id(plugin_id)
    root = root / plugin_id
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"插件目录非空: {root}")
    root.mkdir(parents=True, exist_ok=True)

    name = display_name or plugin_id
    (root / "plugin.py").write_text(
        build_plugin_py(plugin_id=plugin_id, display_name=name, version=version),
        encoding="utf-8",
    )
    (root / "plugin.yaml").write_text(
        PLUGIN_YAML_TEMPLATE.format(
            plugin_id=plugin_id,
            display_name=name,
            version=version,
            today=date.today().isoformat(),
        ),
        encoding="utf-8",
    )
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests" / "test_contract.py").write_text(
        build_test_conftest(plugin_id=plugin_id),
        encoding="utf-8",
    )
    (root / "listing.md").write_text(
        f"# {name}\n\n契约 2 插件（subprocess 隔离）。\n\n"
        "**权限**：见 PLUGIN_METADATA/plugin.yaml（能力面声明）。\n\n"
        "**本地验证**：\n"
        "```bash\n"
        "omnicrawler plugins audit --local .\n"
        "pytest -m plugin_contract\n"
        "```\n",
        encoding="utf-8",
    )
    return root

