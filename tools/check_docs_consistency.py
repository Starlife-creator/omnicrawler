"""Verify the small set of current-facing project facts stays consistent.

Historical release notes and archived compatibility documents deliberately retain
their original versions, so this checker only reads documents that describe the
current release and its supported runtime matrix.

It also enforces the documentation governance rules in ``docs/README.md``: the
index must be reachable from the repository entry points, every current-facing
document must be indexed and carry a version metadata line, and the archive /
release directories must keep their gate pages.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

CURRENT_DOCS = (
    "README.md",
    "OmniCrawler-用户指南.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "docs/README.md",
    "docs/SUPPORT_MATRIX.md",
    "docs/TEST_REPORT.md",
    "docs/ARCHITECTURE.md",
    "docs/CONFIG_REFERENCE.md",
    "docs/CAPABILITY_MATURITY.md",
    "docs/COMPATIBILITY_0.14.0.md",
    "docs/DESKTOP_RUNTIME_1.4.md",
    "docs/GUI_DESIGN_2.1.md",
    "docs/PLUGIN_CONTRACT.md",
    "docs/PRODUCTION_GUIDE.md",
    "docs/WINDOWS_PACKAGING.md",
    "docs/releases/RELEASE_REPORT_0.14.0.md",
    "docs/RESEARCH_AND_FUSION.md",
    "docs/E2E_TEST_REPORT.md",
)

CURRENT_METADATA = (
    "constraints/quality.txt",
    "constraints/README.md",
)

# 文档治理规则：防止导航页与文档互相脱钩，避免 docs/ 变成第二个归档场。
DOC_INDEX = "docs/README.md"
INDEX_ENTRYPOINTS = ("README.md", "CONTRIBUTING.md")
GATE_PAGES = ("docs/archive/README.md", "docs/releases/README.md")
METADATA_MARKER = "> 适用版本："

# 文档里「项目许可」的散文别名 → SPDX 标识（用于许可陈述一致性门禁）。
# 只收**可能被写成项目自身许可**的那几个；第三方组件的许可表不在此列（由行内
# 是否引用 LICENSE 文件进一步收窄，见 check_license_statements）。
LICENSE_ALIASES: dict[str, str] = {
    "Apache License 2.0": "Apache-2.0",
    "Apache-2.0": "Apache-2.0",
    "GNU Affero General Public License v3.0": "AGPL-3.0-only",
    "AGPL-3.0-only": "AGPL-3.0-only",
    "AGPL-3.0-or-later": "AGPL-3.0-or-later",
    "AGPL v3": "AGPL-3.0-only",
    "GNU General Public License v3": "GPL-3.0-only",
    "GPL-3.0-only": "GPL-3.0-only",
    "GPL-3.0-or-later": "GPL-3.0-or-later",
    "MIT License": "MIT",
    "MIT": "MIT",
}


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_project_metadata(root: Path) -> tuple[str, str]:
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    project = metadata["project"]
    return str(project["version"]), str(project["requires-python"])


def load_project_license(root: Path) -> str:
    """项目当前许可（取自 `pyproject.toml`，它才是许可的权威来源）。"""
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    value = metadata["project"]["license"]
    if isinstance(value, dict):
        return str(value.get("text") or value.get("file") or "")
    return str(value)


def minimum_python(requires_python: str) -> str:
    match = re.fullmatch(r">=\s*(\d+\.\d+)", requires_python.strip())
    if match is None:
        raise ValueError(f"Only a simple >= Python requirement is supported: {requires_python!r}")
    return match.group(1)


def coverage_gate(root: Path) -> float:
    import importlib.util

    # 用标准导入机制装载（而非裸 exec 源码），避免代码执行味道（P2-7）
    spec = importlib.util.spec_from_file_location(
        "check_coverage_gates", root / "tools" / "check_coverage_gates.py"
    )
    if spec is None or spec.loader is None:
        raise ValueError("无法加载 tools/check_coverage_gates.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return float(module.OVERALL_COVERAGE_GATE)


def current_config_version(root: Path) -> int:
    source = (root / "src" / "omnicrawler" / "core" / "migrations.py").read_text(encoding="utf-8")
    match = re.search(r"^CURRENT_CONFIG_VERSION\s*=\s*(\d+)\s*$", source, re.MULTILINE)
    if match is None:
        raise ValueError("CURRENT_CONFIG_VERSION is missing")
    return int(match.group(1))


def check_docs_governance(root: Path, texts: dict[str, str]) -> list[str]:
    """Keep docs/ navigable: the index, its entry points and per-doc metadata."""
    issues: list[str] = []
    index = texts.get(DOC_INDEX, "")

    for entry in INDEX_ENTRYPOINTS:
        if DOC_INDEX not in texts.get(entry, ""):
            issues.append(f"{entry}: missing entry link to {DOC_INDEX}")
    for gate in GATE_PAGES:
        if not (root / gate).is_file():
            issues.append(f"missing docs gate page: {gate}")
    if not index:
        return issues

    docs_dir = root / "docs"
    for path in sorted(p for p in docs_dir.glob("*.md") if p.name != "README.md"):
        name = f"docs/{path.name}"
        if f"]({path.name})" not in index:
            issues.append(f"{DOC_INDEX}: missing index entry for {name}")
        if METADATA_MARKER not in path.read_text(encoding="utf-8"):
            issues.append(f"{name}: missing version metadata line ({METADATA_MARKER}...)")
    for path in sorted((docs_dir / "adr").glob("*.md")):
        if f"](adr/{path.name})" not in index:
            issues.append(f"{DOC_INDEX}: missing index entry for docs/adr/{path.name}")
    return issues


def _walkthrough_expected(root: Path) -> dict[str, int]:
    """从 `tools/walkthrough_demo_site.py` 取样本真值（唯一来源）。"""
    import importlib.util

    path = root / "tools" / "walkthrough_demo_site.py"
    if not path.is_file():
        return {}
    spec = importlib.util.spec_from_file_location("_walkthrough_demo_site_for_gate", path)
    if spec is None or spec.loader is None:  # pragma: no cover - 环境异常
        return {}
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return dict(getattr(module, "EXPECTED", {}))


def check_walkthrough_sample_numbers(root: Path) -> list[str]:
    """`docs/MANUAL_WALKTHROUGH.md` 的样本数字必须是 `EXPECTED` 的**投影**。

    该文档写着"样本固定：列表第 1 页 6 条、翻页后合计 8 条、附件 1 个"，
    并声称这些数字由 `EXPECTED` 提供、"不会与文档悄悄漂移"—— 但此前**没有任何门禁
    校验文档文字本身**：改一个样本常量，文档会静默过期，走查者会按过期数字
    误判"翻页失败"。这里把它变成机器校验的投影。
    """
    doc = root / "docs" / "MANUAL_WALKTHROUGH.md"
    if not doc.is_file():
        return []   # 文档缺失由其它检查负责，这里不重复报
    expected = _walkthrough_expected(root)
    if not expected:
        # 文档明确指向该文件；文件缺失或没有 EXPECTED ⇒ 门禁**不能静默放过**
        return [
            "docs/MANUAL_WALKTHROUGH.md 引用的样本来源 tools/walkthrough_demo_site.py "
            "缺失或没有 EXPECTED —— 样本数字无法校验"
        ]
    text = doc.read_text(encoding="utf-8").replace("\r\n", "\n")
    issues: list[str] = []

    sample = re.search(
        r"列表第 1 页 \*\*(?P<page1>\d+)\*\* 条、翻页后合计 \*\*(?P<total>\d+)\*\* 条、"
        r"附件 \*\*(?P<attachments>\d+)\*\* 个",
        text,
    )
    if sample is None:
        issues.append(
            "docs/MANUAL_WALKTHROUGH.md: 找不到「样本固定：列表第 1 页 … 条、翻页后合计 … 条、附件 … 个」"
            " 这句 —— 它必须存在且与 EXPECTED 一致，文档结构若改动请同步本门禁"
        )
    else:
        for key, group in (
            ("list_items", "page1"),
            ("all_items", "total"),
            ("attachments", "attachments"),
        ):
            if int(sample.group(group)) != expected.get(key):
                issues.append(
                    f"docs/MANUAL_WALKTHROUGH.md: 样本数字与 EXPECTED 不一致 —— "
                    f"{group}={sample.group(group)}，EXPECTED[{key!r}]={expected.get(key)}"
                )

    step = re.search(r"列表任务应含翻页后的合计 (?P<total>\d+) 条", text)
    if step is None:
        issues.append(
            "docs/MANUAL_WALKTHROUGH.md: 找不到第 5 步「列表任务应含翻页后的合计 … 条」——"
            " 该数字也必须与 EXPECTED[all_items] 一致"
        )
    elif int(step.group("total")) != expected.get("all_items"):
        issues.append(
            f"docs/MANUAL_WALKTHROUGH.md: 第 5 步写「合计 {step.group('total')} 条」，"
            f"EXPECTED[all_items]={expected.get('all_items')}"
        )
    return issues


def check_license_statements(root: Path, texts: dict[str, str]) -> list[str]:
    """受管文档**声明的项目许可**必须等于 `pyproject.toml` 的 license。

    起因：AGPL → Apache-2.0 迁移后，《用户指南》附录里仍留着 AGPL 全称，
    而**没有任何门禁会发现**——按 `grep AGPL` 还会因为它写的是全称（不含 "AGPL" 字面）
    而得到「已经改好了」的假清白。

    判据：只认**同一行里既引用了 LICENSE 文件、又写出许可名**的陈述行，
    以此避开第三方许可表（那些行不会同时引用本项目的 LICENSE 文件）。
    ★ 并断言「至少找到一条陈述」——找不到即许可陈述失踪，**禁止静默放过**。
    """
    declared = load_project_license(root)
    if not declared:
        return ["pyproject.toml: 读不到 project.license，许可陈述无法比对"]
    issues: list[str] = []
    found = 0
    for label, text in texts.items():
        for line in text.replace("\r\n", "\n").splitlines():
            if "LICENSE" not in line:
                continue
            for alias, canonical in LICENSE_ALIASES.items():
                if alias in line:
                    found += 1
                    if canonical != declared:
                        issues.append(
                            f"{label}: 声明的项目许可是 {canonical}，"
                            f"与 pyproject.toml 的 {declared} 不一致"
                        )
                    break
    if found == 0:
        issues.append(
            "没有任何受管文档声明项目许可 —— 许可陈述失踪；"
            "按 fail-closed 口径，门禁不能把它当成「没有可比对项」而放行"
        )
    return issues


def check(root: Path) -> list[str]:
    version, requires_python = load_project_metadata(root)
    python_version = minimum_python(requires_python)
    gate = coverage_gate(root)
    config_version = current_config_version(root)
    texts = {
        relative: (root / relative).read_text(encoding="utf-8")
        for relative in CURRENT_DOCS
        if (root / relative).is_file()
    }
    metadata_texts = {
        relative: (root / relative).read_text(encoding="utf-8")
        for relative in CURRENT_METADATA
        if (root / relative).is_file()
    }
    issues: list[str] = []
    required_files = set(CURRENT_DOCS) - set(texts)
    issues.extend(f"missing current-facing document: {path}" for path in sorted(required_files))
    required_metadata = set(CURRENT_METADATA) - set(metadata_texts)
    issues.extend(f"missing current release metadata: {path}" for path in sorted(required_metadata))

    readme = texts.get("README.md", "")
    support = texts.get("docs/SUPPORT_MATRIX.md", "")
    contributing = texts.get("CONTRIBUTING.md", "")
    workflow = (root / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    configured_gate = float(
        pyproject.get("tool", {}).get("coverage", {}).get("report", {}).get("fail_under", 0)
    )

    # 版本真源自 P1-3 收尾起位于叶子模块 _version.py（__init__ 惰性 re-export）
    source = (root / "src" / "omnicrawler" / "_version.py").read_text(encoding="utf-8")
    source_match = re.search(r'^__version__\s*=\s*"([^"]+)"\s*$', source, re.MULTILINE)
    if source_match is None or source_match.group(1) != version:
        found = source_match.group(1) if source_match else "missing"
        issues.append(f"src/omnicrawler/_version.py: version {found} does not match pyproject {version}")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    if not re.search(rf"^## {re.escape(version)}\s+-\s+\d{{4}}-\d{{2}}-\d{{2}}$", changelog, re.MULTILINE):
        issues.append(f"CHANGELOG.md: missing dated current release heading {version}")

    for label, text in (("README.md", readme), ("docs/SUPPORT_MATRIX.md", support)):
        if f"{version}" not in text:
            issues.append(f"{label}: missing current version {version}")
        if f"Python {python_version}+" not in text:
            issues.append(f"{label}: missing supported Python statement Python {python_version}+")
    versioned_docs = (
        "OmniCrawler-用户指南.md",
        "docs/README.md",
        "docs/ARCHITECTURE.md",
        "docs/CONFIG_REFERENCE.md",
        "docs/CAPABILITY_MATURITY.md",
        "docs/COMPATIBILITY_0.14.0.md",
        "docs/DESKTOP_RUNTIME_1.4.md",
        "docs/GUI_DESIGN_2.1.md",
        "docs/PLUGIN_CONTRACT.md",
        "docs/PRODUCTION_GUIDE.md",
        "docs/WINDOWS_PACKAGING.md",
        "docs/releases/RELEASE_REPORT_0.14.0.md",
        "docs/E2E_TEST_REPORT.md",
    )
    for label in versioned_docs:
        if version not in texts.get(label, ""):
            issues.append(f"{label}: missing current version {version}")
    for label, text in metadata_texts.items():
        if version not in text:
            issues.append(f"{label}: missing current version {version}")
    if f"config_version: {config_version}" not in texts.get("docs/CONFIG_REFERENCE.md", ""):
        issues.append(f"docs/CONFIG_REFERENCE.md: missing config_version: {config_version}")
    coverage_markers = {f">= {gate:g}%", f"≥{gate:g}%", f"≥ {gate:g}%"}
    if not any(marker in readme for marker in coverage_markers):
        issues.append(f"README.md: missing overall coverage gate >= {gate:g}%")
    if not any(marker in contributing for marker in coverage_markers):
        issues.append(f"CONTRIBUTING.md: missing overall coverage gate >= {gate:g}%")
    if configured_gate != gate:
        issues.append(f"coverage gate mismatch: pyproject={configured_gate:g}% tool={gate:g}%")
    if f'"{python_version}"' not in workflow:
        issues.append(f"quality workflow does not exercise the minimum Python {python_version}")

    # F46：便携包随附文本/启动器纳入一致性检查（防 F44/F45 复发）
    portable_readme = root / "packaging" / "PORTABLE_README.txt"
    launcher = root / "packaging" / "OmniCrawler-Launcher.bat"
    if not launcher.is_file():
        issues.append("packaging/OmniCrawler-Launcher.bat missing")
    if not portable_readme.is_file():
        issues.append("packaging/PORTABLE_README.txt missing")
    else:
        readme_text = portable_readme.read_text(encoding="utf-8")
        for mentioned_name in re.findall(r"[“\"]([\w.-]+\.bat)[”\"]", readme_text):
            if not (root / "packaging" / mentioned_name).is_file():
                issues.append(f"PORTABLE_README.txt 提到 {mentioned_name} 但 packaging 下不存在")
        if re.search(r"OmniCrawler\s+\d+\.\d+(?:\.\d+)?", readme_text):
            issues.append("PORTABLE_README.txt 硬编码版本号，应去掉或由构建渲染")

    issues.extend(check_walkthrough_sample_numbers(root))
    issues.extend(check_docs_governance(root, texts))
    issues.extend(check_license_statements(root, texts))
    return issues


def main() -> int:
    root = project_root()
    issues = check(root)
    if issues:
        print("Current-project consistency check failed:", file=sys.stderr)
        for issue in issues:
            print(f"- {issue}", file=sys.stderr)
        return 1
    version, requires_python = load_project_metadata(root)
    print(f"Current-project consistency check passed: OmniCrawler {version}, Python {requires_python}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
