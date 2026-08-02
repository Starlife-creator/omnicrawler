"""Plugin SDK scaffolding and contract checks for signed, permission-scoped plugins."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path

from .plugin_sandbox import PluginPackageManifest

_ID = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")


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

