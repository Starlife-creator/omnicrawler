"""Generate a CycloneDX dependency SBOM covering the **full transitive closure**.

Fixes for S40 (was: direct deps only, duplicate entries, bogus
``not-installed-optional`` purls, no dependency graph, irreproducible):

1. **Transitive closure** — BFS over each installed package's ``Requires-Dist``
   so the SBOM covers every runtime dependency, not just the direct ones.
2. **Deduplication** — one component per package name (highest seen version);
   the old code counted one entry per ``Requires-Dist`` line, duplicating
   packages across extra declarations (``lxml`` x3, ``openpyxl`` x3 ...).
3. **Valid versions only** — optional extras that are not installed are
   collected as warnings instead of being emitted with a fake version
   ``not-installed-optional`` which breaks CycloneDX consumers.
4. **Dependency graph** — ``dependencies`` array maps every component to the
   packages it pulls in.
5. **Reproducibility guard** — the generated file is only as reproducible as
   the environment it runs in; ``tools/check_guardrails.py`` compares the SBOM
   against ``pip freeze`` of the same environment to keep them in sync.
6. **Extra-aware expansion** — transitive expansion honors pip semantics: a
   requirement guarded by ``extra == "<name>"`` is only followed when
   ``<name>`` was requested on the edge that pulled the package in (e.g.
   ``psycopg[binary]`` → ``psycopg-binary; extra == "binary"`` IS collected,
   but httpx's ``click; extra == "cli"`` is not, because no edge requests
   httpx[cli]). Previously every ``Requires-Dist`` line was followed, so
   click/colorama leaked into the SBOM whenever the generation environment
   happened to preinstall them (windows-latest runners do), tripping the
   ``SBOM ⊆ pip freeze`` gate of ``check_guardrails.py`` (release run
   32264465809 aggregate failure). Platform/version markers are left to the
   existing "not installed → skip" fallback (the guardrail gate tolerates
   freeze ⊋ SBOM).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_NAME_RE = re.compile(r"^([A-Za-z0-9_.-]+)")
_EDGE_EXTRAS_RE = re.compile(r"\[([^\]]*)\]")
_EXTRA_EQ_RE = re.compile(r"""extra\s*==\s*['"]([^'"]+)['"]""")


def _requested_extras(requirement: str) -> frozenset[str]:
    """解析需求名部分的 extras 请求（``psycopg[binary]<4`` → {"binary"}）。"""
    name_part = requirement.split(";", 1)[0]
    match = _EDGE_EXTRAS_RE.search(name_part)
    if not match:
        return frozenset()
    return frozenset(part.strip() for part in match.group(1).split(",") if part.strip())


def _gating_extras(requirement: str) -> frozenset[str] | None:
    """返回门控该需求的 extra 名集合；无 extra 门控返回 None。

    pip 语义：``Requires-Dist: X; extra == "e"`` 仅当拉入当前包的边显式
    请求了 ``e``（如 ``pkg[e]``）时才安装。传递展开必须遵守同样的门控，
    否则 httpx 的 ``click; extra == "cli"`` 这类未启用功能会泄漏进 SBOM。
    含 ``extra`` 关键字但无法解析为 ``==`` 形式的（如 ``extra !=``，实际
    不存在），保守视为门控（fail-closed，不展开）。
    """
    if "extra" not in requirement:
        return None
    names = frozenset(_EXTRA_EQ_RE.findall(requirement))
    return names  # 空集 = 无法识别的 extra 表达式，按门控处理


def _requires_of(name: str) -> list[str]:
    try:
        distribution = importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        return []
    return [requirement for requirement in (distribution.requires or []) if requirement]


def _normalise_name(name: str) -> str:
    # PyPI normalises: case-insensitive, runs of -_. collapse to single _
    return re.sub(r"[-_.]+", "-", name).lower().replace("-", "_")


def _purl_name(name: str) -> str:
    # PEP 503 / CycloneDX purl：小写、-_. 运行折叠为连字符（pkg:pypi/foo-bar）
    return re.sub(r"[-_.]+", "-", name).lower()


def _license(metadata: Any) -> str:
    expression = metadata.get("License-Expression")
    if expression:
        return str(expression)
    license_value = metadata.get("License")
    if license_value and len(str(license_value)) < 100:
        return str(license_value)
    classifiers = metadata.get_all("Classifier") or []
    matches = [value.rsplit("::", 1)[-1].strip() for value in classifiers if "License ::" in value]
    return ", ".join(matches) if matches else "NOASSERTION"


def _uuid_from_hash(value: str) -> str:
    digest = hashlib.sha256(value.encode()).hexdigest()[:32]
    return f"{digest[:8]}-{digest[8:12]}-5{digest[13:16]}-a{digest[17:20]}-{digest[20:32]}"


def _requirement_name(requirement: str) -> str:
    match = _NAME_RE.match(requirement)
    if match is None:
        raise ValueError(f"无法解析依赖声明: {requirement!r}")
    return match.group(1)


def _find_distribution(name: str) -> importlib.metadata.Distribution:
    """按原始声明名查找；PEP 503 归一化名（-/_. 互换）兜底。"""
    try:
        return importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        return importlib.metadata.distribution(_normalise_name(name))


def build_sbom() -> dict[str, Any]:
    project = importlib.metadata.distribution("omnicrawler-platform")
    # 根种子：project.requires 全声明连同各边请求的 extras 入队。extra == 门控的
    # 声明也照样入队：已安装（当前环境对应 extras）就正常展开，未安装走下方
    # PackageNotFoundError 跳过——SBOM 反映真实环境。
    queue: deque[tuple[str, frozenset[str]]] = deque(
        (_requirement_name(requirement), _requested_extras(requirement))
        for requirement in (project.requires or [])
    )
    components: dict[str, dict[str, Any]] = {}
    dependencies: dict[str, list[str]] = {}
    warnings: list[str] = []
    seen_extras: dict[str, frozenset[str]] = {}

    while queue:
        raw_name, edge_extras = queue.popleft()
        key = _normalise_name(raw_name)
        # fixpoint：同包可能经多条边到达、各请求不同 extras（如 psycopg[binary]）；
        # 合并后无新增 extras 则跳过，否则（重新）展开。
        merged = seen_extras.get(key, frozenset()) | edge_extras
        if key in seen_extras and merged <= seen_extras[key]:
            continue
        seen_extras[key] = merged
        try:
            distribution = _find_distribution(raw_name)
        except importlib.metadata.PackageNotFoundError:
            warnings.append(f"optional extra not installed, skipped: {raw_name}")
            continue
        version = distribution.version
        ref = f"pkg:pypi/{_purl_name(raw_name)}@{version}"
        components[key] = {
            "type": "library",
            "name": distribution.metadata.get("Name") or raw_name,
            "version": version,
            "licenses": [{"license": {"name": _license(distribution.metadata)}}],
            "purl": ref,
            "bom-ref": ref,
        }
        # 中间节点展开（pip 语义）：extra == "e" 门控的声明仅当拉入当前包的某条边
        # 请求了 e 才展开；未门控声明始终展开。每条依赖携带其自身边上声明的
        # extras（如 dep[foo]），供下一层门控判断。
        deps: dict[str, frozenset[str]] = {}
        for dep in distribution.requires or []:
            gating = _gating_extras(dep)
            if gating is not None and not (gating & merged):
                continue
            dep_name = _requirement_name(dep)
            deps[dep_name] = deps.get(dep_name, frozenset()) | _requested_extras(dep)
        dependencies[ref] = sorted(deps)
        for dep_name, dep_extras in sorted(deps.items()):
            queue.append((dep_name, dep_extras))

    # 依赖图只保留已解析进 components 的引用，避免悬空名（P2-5）
    ref_by_norm = {name: item["bom-ref"] for name, item in components.items()}
    resolved_dependencies: dict[str, list[str]] = {}
    for ref, raw_deps in dependencies.items():
        resolved_dependencies[ref] = sorted(
            {
                ref_by_norm[_normalise_name(dep)]
                for dep in raw_deps
                if _normalise_name(dep) in ref_by_norm
            }
        )
    dependencies = resolved_dependencies

    document = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": "urn:uuid:" + _uuid_from_hash(f"omnicrawler-platform:{project.version}:transitive"),
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(UTC).isoformat(),
            "component": {"type": "application", "name": "omnicrawler-platform", "version": project.version},
        },
        "components": sorted(components.values(), key=lambda item: item["name"].casefold()),
        "dependencies": [
            {"ref": ref, "dependsOn": dependencies.get(ref, [])}
            for ref in sorted(dependencies)
        ],
        "properties": [{"name": "omnicrawler:generator", "value": "tools/generate_sbom.py (transitive)"}],
    }
    document["metadata"]["properties"] = [{"name": "omnicrawler:skipped-optional", "value": str(len(warnings))}]
    return document


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a CycloneDX SBOM covering the transitive closure")
    parser.add_argument("--output", default="dist/omnicrawler-sbom.cdx.json")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = (root / args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(build_sbom(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
