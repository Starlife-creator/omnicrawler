"""Verify that every installed runtime component in a CycloneDX SBOM matches uv.lock."""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from pathlib import Path


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).casefold()


def check(sbom_path: Path, lock_path: Path) -> list[str]:
    if not sbom_path.is_file():
        return [f"SBOM 不存在: {sbom_path}"]
    if not lock_path.is_file():
        return [f"锁文件不存在: {lock_path}"]
    try:
        sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return [f"SBOM 无法解析: {exc}"]
    try:
        lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        return [f"锁文件无法解析: {exc}"]

    locked: dict[str, set[str]] = {}
    for package in lock.get("package", []):
        if not isinstance(package, dict) or not package.get("name") or not package.get("version"):
            continue
        locked.setdefault(_normalise(str(package["name"])), set()).add(str(package["version"]))

    components = sbom.get("components", []) if isinstance(sbom, dict) else []
    if not isinstance(components, list) or not components:
        return ["SBOM components 为空"]
    issues: list[str] = []
    for component in components:
        if not isinstance(component, dict):
            issues.append("SBOM components 含非对象条目")
            continue
        name = str(component.get("name", "")).strip()
        version = str(component.get("version", "")).strip()
        versions = locked.get(_normalise(name))
        if not versions:
            issues.append(f"SBOM 包未进入 uv.lock: {name}=={version}")
        elif version not in versions:
            issues.append(
                f"SBOM 版本偏离 uv.lock: {name}=={version}（锁定: {', '.join(sorted(versions))}）"
            )
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 CycloneDX SBOM 是否来自 uv.lock")
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--lock", type=Path, default=Path("uv.lock"))
    args = parser.parse_args()
    issues = check(args.sbom, args.lock)
    if issues:
        print(f"SBOM 与锁文件不一致（{len(issues)} 项）：")
        for issue in issues:
            print(f"  - {issue}")
        return 1
    print("SBOM 与 uv.lock 一致：所有已安装运行时组件均使用锁定版本")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
