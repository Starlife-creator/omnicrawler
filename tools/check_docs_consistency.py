"""Verify the small set of current-facing project facts stays consistent.

Historical release notes and archived compatibility documents deliberately retain
their original versions, so this checker only reads documents that describe the
current release and its supported runtime matrix.
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
    "docs/COMPATIBILITY_0.8.0.md",
    "docs/DESKTOP_RUNTIME_1.4.md",
    "docs/GUI_DESIGN_2.1.md",
    "docs/PLUGIN_CONTRACT.md",
    "docs/PRODUCTION_GUIDE.md",
    "docs/WINDOWS_PACKAGING.md",
    "docs/releases/RELEASE_REPORT_0.8.0.md",
    "docs/E2E_TEST_REPORT.md",
)

CURRENT_METADATA = (
    "constraints/quality.txt",
    "constraints/README.md",
)


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_project_metadata(root: Path) -> tuple[str, str]:
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    project = metadata["project"]
    return str(project["version"]), str(project["requires-python"])


def minimum_python(requires_python: str) -> str:
    match = re.fullmatch(r">=\s*(\d+\.\d+)", requires_python.strip())
    if match is None:
        raise ValueError(f"Only a simple >= Python requirement is supported: {requires_python!r}")
    return match.group(1)


def coverage_gate(root: Path) -> float:
    namespace = {"__name__": "coverage_gate_check"}
    exec((root / "tools" / "check_coverage_gates.py").read_text(encoding="utf-8"), namespace)
    return float(namespace["OVERALL_COVERAGE_GATE"])


def current_config_version(root: Path) -> int:
    source = (root / "src" / "omnicrawl" / "core" / "migrations.py").read_text(encoding="utf-8")
    match = re.search(r"^CURRENT_CONFIG_VERSION\s*=\s*(\d+)\s*$", source, re.MULTILINE)
    if match is None:
        raise ValueError("CURRENT_CONFIG_VERSION is missing")
    return int(match.group(1))


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
    configured_gate = float(pyproject.get("tool", {}).get("coverage", {}).get("report", {}).get("fail_under", 0))

    source = (root / "src" / "omnicrawl" / "__init__.py").read_text(encoding="utf-8")
    source_match = re.search(r'^__version__\s*=\s*"([^"]+)"\s*$', source, re.MULTILINE)
    if source_match is None or source_match.group(1) != version:
        found = source_match.group(1) if source_match else "missing"
        issues.append(f"src/omnicrawl/__init__.py: version {found} does not match pyproject {version}")
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
        "docs/COMPATIBILITY_0.8.0.md",
        "docs/DESKTOP_RUNTIME_1.4.md",
        "docs/GUI_DESIGN_2.1.md",
        "docs/PLUGIN_CONTRACT.md",
        "docs/PRODUCTION_GUIDE.md",
        "docs/WINDOWS_PACKAGING.md",
        "docs/releases/RELEASE_REPORT_0.8.0.md",
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
