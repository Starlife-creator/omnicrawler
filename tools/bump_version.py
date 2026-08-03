#!/usr/bin/env python3
"""全项目版本号自动更新工具。

用法::

    python tools/bump_version.py 新版本号 [--no-git] [-m "自定义摘要"]

示例::

    python tools/bump_version.py 2.8.0
    python tools/bump_version.py 0.1.0 --no-git
    python tools/bump_version.py 2.8.0 -m "修复关键Bug并发布"

步骤:
    1. 读取 pyproject.toml 旧版本号
    2. 更新 pyproject.toml / __init__.py / 约束文件
    3. 重命名版本化文件名
    4. 替换所有文档正文中的旧版本号
    5. 同步 check_docs_consistency.py
    6. 更新 CHANGELOG.md
    7. 自动替换 YAML 模板中的 OmniCrawler/X.Y
    8. 扫描 .py 源码硬编码版本号（安全网）
    9. 自验证 (check_docs_consistency.py)
   10. Git 操作 (add / commit / tag，可用 --no-git 跳过)

架构说明:
    项目版本号唯一数据源在 pyproject.toml (project.version) 和
    src/omnicrawl/__init__.py (__version__)。所有 User-Agent 字符串均通过
    core.utils.user_agent() 动态生成，无需手动维护各模块的版本号。
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tomllib
from datetime import date
from pathlib import Path

# ── 版本化文件清单（文件名含版本号，需要重命名） ──────────────────────────
_VERSIONED_FILES: tuple[tuple[str, str], ...] = (
    # (带旧版版本号占位符的相对路径, 带新版版本号占位符的相对路径)
    ("OmniCrawler-{old}-Agent-Context.md", "OmniCrawler-{new}-Agent-Context.md"),
    ("OmniCrawler-{old}-Agent-Prompt.md", "OmniCrawler-{new}-Agent-Prompt.md"),
    ("docs/COMPATIBILITY_{old}.md", "docs/COMPATIBILITY_{new}.md"),
    ("docs/OPTIMIZATION_PLAN_FIRST_PRINCIPLES_{old}.md", "docs/OPTIMIZATION_PLAN_FIRST_PRINCIPLES_{new}.md"),
    ("docs/releases/RELEASE_REPORT_{old}.md", "docs/releases/RELEASE_REPORT_{new}.md"),
)

# ── 正文含版本号的文档清单（替换 "旧版本" → "新版本"） ──────────────────
_TEXT_REPLACE_FILES: tuple[str, ...] = (
    "README.md",
    "E2E_TEST_REPORT.md",
    "docs/README.md",
    "docs/SUPPORT_MATRIX.md",
    "docs/ARCHITECTURE.md",
    "docs/CAPABILITY_MATURITY.md",
    "docs/CONFIG_REFERENCE.md",
    "docs/DESKTOP_RUNTIME_1.4.md",
    "docs/DISTRIBUTED.md",
    "docs/GUI_DESIGN_2.1.md",
    "docs/PLUGIN_CONTRACT.md",
    "docs/PRODUCTION_GUIDE.md",
    "docs/TEST_REPORT.md",
    "docs/WINDOWS_PACKAGING.md",
    "OmniCrawler-用户指南.md",
    # 下面用 {old} 占位，在运行时用实际版本号填入
    "OmniCrawler-{old}-Agent-Context.md",
    "OmniCrawler-{old}-Agent-Prompt.md",
    "docs/COMPATIBILITY_{old}.md",
    "docs/OPTIMIZATION_PLAN_FIRST_PRINCIPLES_{old}.md",
    "docs/releases/RELEASE_REPORT_{old}.md",
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="全项目版本号自动更新",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("version", help="新版本号，如 2.8.0")
    parser.add_argument("--no-git", action="store_true", help="跳过 git add/commit/tag")
    parser.add_argument(
        "-m", "--message", metavar="MSG",
        help="自定义 CHANGELOG 变更摘要（覆盖自动 git log）",
    )
    return parser.parse_args(argv)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _read_old_version(root: Path) -> str:
    """从 pyproject.toml 读取当前版本号。"""
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(pyproject["project"]["version"])


def _validate_version(version: str) -> None:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SystemExit(f"错误：版本号格式非法，需要 X.Y.Z 格式，收到: {version!r}")


def _replace_in_file(filepath: Path, old_str: str, new_str: str) -> bool:
    """替换文件中的字符串，返回是否实际做了修改。"""
    text = filepath.read_text(encoding="utf-8")
    if old_str not in text:
        return False
    filepath.write_text(text.replace(old_str, new_str), encoding="utf-8")
    return True


def _replace_version_in_file(filepath: Path, old: str, new: str) -> bool:
    """替换文件中的版本号字符串，返回是否实际做了修改。"""
    return _replace_in_file(filepath, old, new)


def step_update_core_files(root: Path, old: str, new: str) -> None:
    """Step 2: 更新 pyproject.toml 和 __init__.py。"""
    print(f"  [core] pyproject.toml: {old} → {new}")

    pyproject_path = root / "pyproject.toml"
    pyproject_text = pyproject_path.read_text(encoding="utf-8")
    # 只替换 [project] 段下的 version 行
    updated = re.sub(
        r'^(version\s*=\s*)"[^"]*"',
        rf'\g<1>"{new}"',
        pyproject_text,
        count=1,
        flags=re.MULTILINE,
    )
    pyproject_path.write_text(updated, encoding="utf-8")

    init_path = root / "src" / "omnicrawl" / "__init__.py"
    print(f"  [core] src/omnicrawl/__init__.py: __version__ {old} → {new}")
    _replace_in_file(init_path, f'__version__ = "{old}"', f'__version__ = "{new}"')


def step_update_constraints(root: Path, old: str, new: str) -> None:
    """Step 3: 更新约束文件中的版本号注释。"""
    qf = root / "constraints" / "quality.txt"
    if _replace_in_file(qf, f"OmniCrawler {old}", f"OmniCrawler {new}"):
        print(f"  [constraints] quality.txt: OmniCrawler {old} → {new}")

    cr = root / "constraints" / "README.md"
    if _replace_in_file(cr, f"OmniCrawler {old}", f"OmniCrawler {new}"):
        print(f"  [constraints] README.md: OmniCrawler {old} → {new}")


def step_rename_versioned_files(root: Path, old: str, new: str) -> dict[str, str]:
    """Step 4: 重命名文件名含版本号的文件。返回 {旧相对路径: 新相对路径}。"""
    print(f"\n  ── 重命名版本化文件 (文件名 {old} → {new}) ──")
    renamed: dict[str, str] = {}
    for old_pattern, new_pattern in _VERSIONED_FILES:
        old_rel = old_pattern.format(old=old)
        new_rel = new_pattern.format(new=new)
        old_path = root / old_rel
        new_path = root / new_rel
        if old_path.exists():
            old_path.rename(new_path)
            print(f"     ✓ {old_rel} → {new_rel}")
            renamed[old_rel] = new_rel
        else:
            print(f"     ⚠ 不存在，跳过: {old_rel}")
    return renamed


def step_replace_text_in_docs(root: Path, old: str, new: str) -> None:
    """Step 5: 在所有文档正文中替换旧版本号字符串。"""
    print(f"\n  ── 替换文档正文 ({old} → {new}) ──")
    for file_pattern in _TEXT_REPLACE_FILES:
        rel = file_pattern.format(old=old)  # 运行时填入实际旧版本号（可能已重命名）
        fpath = root / rel
        if not fpath.is_file():
            # 尝试用新版本号路径找（已重命名的文件）
            rel_new = file_pattern.format(old=new)
            fpath_new = root / rel_new
            if fpath_new.is_file():
                fpath = fpath_new
                rel = rel_new
            else:
                print(f"     ⚠ 不存在，跳过: {rel}")
                continue
        count = fpath.read_text(encoding="utf-8").count(old)
        if count > 0:
            _replace_in_file(fpath, old, new)
            print(f"     ✓ {rel} ({count} 处)")
        else:
            print(f"     - {rel} (无匹配)")


def step_sync_check_docs_consistency(root: Path, old: str, new: str) -> None:
    """Step 6: 同步 tools/check_docs_consistency.py 中的版本化文件名。"""
    print("\n  ── 同步 check_docs_consistency.py ──")

    check_path = root / "tools" / "check_docs_consistency.py"
    text = check_path.read_text(encoding="utf-8")

    # 只替换 CURRENT_DOCS 和 versioned_docs 两个元组内容中的版本号
    # 这两个元组跨越连续行，我们逐行处理
    lines = text.splitlines(keepends=True)
    in_current_docs = False
    in_versioned_docs = False
    modified = False
    new_lines: list[str] = []

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("CURRENT_DOCS = ("):
            in_current_docs = True
        elif in_current_docs and stripped == ")":
            in_current_docs = False
        elif stripped.startswith("versioned_docs = ("):
            in_versioned_docs = True
        elif in_versioned_docs and stripped == ")":
            in_versioned_docs = False

        if (in_current_docs or in_versioned_docs) and old in line:
            new_line = line.replace(old, new)
            if new_line != line:
                print(f"     ✓ {stripped[:60]}...")
                modified = True
            new_lines.append(new_line)
        else:
            new_lines.append(line)

    if modified:
        check_path.write_text("".join(new_lines), encoding="utf-8")
    else:
        print("     - 无需更新 (已是最新)")


def _get_changelog_entries(root: Path, old_version: str) -> str:
    """从 git log 提取变更摘要。"""
    # 尝试从旧版本 tag 到 HEAD
    try:
        result = subprocess.run(
            ["git", "log", f"v{old_version}..HEAD", "--pretty=format:- %s"],
            capture_output=True, text=True, cwd=str(root),
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass

    # 回退：取最近 15 条 commit
    try:
        result = subprocess.run(
            ["git", "log", "--pretty=format:- %s", "-n", "15"],
            capture_output=True, text=True, cwd=str(root),
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass

    return "版本更新"


def step_update_changelog(
    root: Path, new: str, custom_message: str | None = None,
) -> None:
    """Step 7: 在 CHANGELOG.md 插入新版本条目。"""
    print("\n  ── 更新 CHANGELOG.md ──")

    changelog_path = root / "CHANGELOG.md"
    text = changelog_path.read_text(encoding="utf-8")

    today = date.today().isoformat()
    heading = f"## {new} - {today}"

    if heading in text:
        print(f"     ⚠ {heading} 已存在，跳过")
        return

    if custom_message:
        summary = custom_message
    else:
        old_version = _read_old_version(root)
        summary = _get_changelog_entries(root, old_version)

    entry = f"\n{heading}\n\n### 变更\n\n{summary}\n"

    # 在 ## Unreleased 之后插入
    if "## Unreleased" in text:
        text = text.replace("## Unreleased", f"## Unreleased{entry}", 1)
    else:
        # 在文件头之后插入
        text = text.replace("# Changelog\n", f"# Changelog\n{entry}", 1)

    changelog_path.write_text(text, encoding="utf-8")
    print(f"     ✓ 新增 {heading}")


def step_fix_template_versions(root: Path, new: str) -> None:
    """Step 7a: 自动替换 YAML 模板中的硬编码 OmniCrawler/X.Y 版本号。

    YAML 配置模板是静态文件，无法调用 Python 的 user_agent()，
    因此此处用正则自动替换为当前版本号。
    """
    print("\n  ── 修复 YAML 模板版本号 ──")
    templates_dir = root / "src" / "omnicrawl" / "templates"
    ua_pattern = re.compile(r"OmniCrawler/[\d.]+")
    fixed_count = 0
    fixed_files: list[str] = []

    if not templates_dir.is_dir():
        print("     - 无 templates 目录，跳过")
        return

    for yaml_file in sorted(templates_dir.rglob("*.yaml")):
        try:
            text = yaml_file.read_text(encoding="utf-8")
            if not ua_pattern.search(text):
                continue
            new_text = ua_pattern.sub(f"OmniCrawler/{new}", text)
            if new_text == text:
                continue
            yaml_file.write_text(new_text, encoding="utf-8")
            count = len(ua_pattern.findall(text))
            fixed_count += count
            fixed_files.append(str(yaml_file.relative_to(root)))
            print(f"     ✓ {yaml_file.relative_to(root)} ({count} 处)")
        except Exception:
            continue

    if fixed_count:
        print(f"     共修复 {fixed_count} 处，涉及 {len(fixed_files)} 个文件")
    else:
        print("     - 无需修复")


def step_scan_hardcoded_py_versions(root: Path, new: str) -> None:
    """Step 7b: 扫描 .py 源码中可能遗漏的硬编码 OmniCrawler/X.Y 版本号。

    自当前版本起，所有 User-Agent 已通过 core.utils.user_agent() 动态生成，
    此步骤作为安全网确保没有人在无意中重新引入硬编码版本号。
    只扫描 .py 文件（.yaml/.yml 已在 step_fix_template_versions 中自动替换）。
    """
    print("\n  ── 扫描 Python 源码硬编码版本号 ──")
    src = root / "src" / "omnicrawl"
    hardcoded_pattern = re.compile(r"OmniCrawler/[\d.]+")
    found: list[tuple[Path, int, str]] = []

    for pyfile in sorted(src.rglob("*.py")):
        # 跳过 venv 和 __pycache__
        if ".venv" in str(pyfile) or "__pycache__" in str(pyfile):
            continue
        try:
            for i, line in enumerate(pyfile.read_text(encoding="utf-8").splitlines(), 1):
                if hardcoded_pattern.search(line):
                    found.append((pyfile.relative_to(root), i, line.strip()))
        except Exception:
            continue

    if found:
        print(f"     ⚠ 发现 {len(found)} 处硬编码版本号 (应使用 user_agent()):")
        for fpath, lineno, text in found:
            print(f"       {fpath}:{lineno}  {text[:100]}")
        print("     提示: 将硬编码替换为 core.utils.user_agent() 调用后重新运行。")
    else:
        print("     ✓ Python 源码中无硬编码版本号 — user_agent() 全覆盖")


def step_self_validate(root: Path) -> None:
    """Step 8: 运行 check_docs_consistency.py 自验证。"""
    print("\n  ── 自验证 (check_docs_consistency.py) ──")

    check_script = root / "tools" / "check_docs_consistency.py"
    result = subprocess.run(
        [sys.executable, str(check_script)],
        capture_output=True, text=True, cwd=str(root),
        timeout=60,
    )

    if result.returncode == 0:
        print(f"     ✓ {result.stdout.strip()}")
    else:
        print("     ✗ 一致性校验失败：", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(1)


def step_git_operations(root: Path, new: str) -> None:
    """Step 9: git add / commit / tag。"""
    print("\n  ── Git 操作 ──")

    def _git(args: list[str], desc: str) -> None:
        print(f"     $ git {' '.join(args)}")
        result = subprocess.run(
            ["git"] + args,
            capture_output=True, text=True, cwd=str(root),
            timeout=30,
        )
        if result.returncode != 0:
            print(f"     ✗ {desc} 失败: {result.stderr.strip()}")
            raise SystemExit(1)
        if result.stdout.strip():
            print(f"       {result.stdout.strip()}")

    _git(["add", "-A"], "暂存所有变更")

    message = f"release: bump to {new}"
    _git(["commit", "-m", message], "提交")

    _git(["tag", f"v{new}"], f"打标签 v{new}")

    print(f"\n  ✓ Git 操作完成: commit + tag v{new}")
    print("    提示: 运行 git push --tags 推送到远程")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    new = args.version
    _validate_version(new)

    root = _project_root()
    os.chdir(str(root))

    old = _read_old_version(root)
    if old == new:
        print(f"当前版本已是 {new}，无需更新。")
        return 0

    print(f"OmniCrawler 版本号自动更新: {old} → {new}")
    print("=" * 60)

    # ── Step 2: 更新核心文件 ──
    print("\n[1/7] 更新核心文件")
    step_update_core_files(root, old, new)

    # ── Step 3: 更新约束文件 ──
    print("\n[2/7] 更新约束文件")
    step_update_constraints(root, old, new)

    # ── Step 4: 重命名版本化文件 ──
    print("\n[3/7] 重命名版本化文件")
    step_rename_versioned_files(root, old, new)

    # ── Step 5: 替换文档正文 ──
    print("\n[4/7] 替换文档正文中的版本号")
    step_replace_text_in_docs(root, old, new)

    # ── Step 6: 同步 check_docs_consistency.py ──
    print("\n[5/7] 同步 check_docs_consistency.py")
    step_sync_check_docs_consistency(root, old, new)

    # ── Step 7: 更新 CHANGELOG.md ──
    print("\n[6/7] 更新 CHANGELOG.md")
    step_update_changelog(root, new, args.message)

    # ── Step 7a: 修复 YAML 模板版本号 ──
    print("\n[7/9] 修复 YAML 模板版本号")
    step_fix_template_versions(root, new)

    # ── Step 7b: 扫描 Python 源码硬编码版本号 ──
    print("\n[8/9] 扫描 Python 源码硬编码版本号")
    step_scan_hardcoded_py_versions(root, new)

    # ── Step 8: 自验证 ──
    print("\n[9/9] 自验证")
    step_self_validate(root)

    # ── Step 9: Git 操作 ──
    if args.no_git:
        print("\n  --no-git: 跳过 git 操作")
    else:
        step_git_operations(root, new)

    print(f"\n{'=' * 60}")
    print(f"✓ 版本号更新完成: {old} → {new}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
