"""Verify that every install/build profile matches its declaration in constraints/profiles.toml.

## 问题

同一个「标准安装」在仓库里有 5 套互不相同的写法（三平台构建脚本一套、两个 setup
脚本的 standard 另一套、Windows 安装脚本直接 full、Dockerfile 只装三项）。没有真源，
改一处不会提醒另一处；`FAQ.md` 还声称「默认 standard」，与 `install_windows.ps1` 的
实际行为相反。

## 判据

`constraints/profiles.toml` 是档位的唯一真源。本门禁逐条到脚本里**读字面量**比对：

- 每个 profile 的 extras 必须能在 pyproject 的 `[project.optional-dependencies]` 里找到；
- 脚本里该行必须与声明**逐字符一致**（顺序也算，因为 uv/pip 参数顺序影响可复现性）；
- 脚本文件不存在 ⇒ 红（改名必须同步声明）；
- 声明里出现脚本中找不到的 profile ⇒ 红（防「声明了但没人用」）；
- 一个 profile 都没解析出来 ⇒ 红（空集合会让所有断言恒真）。

## 为什么是「比对字面量」而不是「从 TOML 生成」

构建脚本分别在 bash / pwsh / Dockerfile 三种执行环境里跑。让它们都去解析 TOML，会把
「档位正确性」依赖到另一种运行时上，并让构建脚本在没有 tomllib 的环境里失败。所以
真源负责**声明**，门禁负责**对齐**：漂移时门禁指名哪个文件哪一行不一致。
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILES_PATH = REPO_ROOT / "constraints" / "profiles.toml"

# profile 名 → (脚本相对路径, 定位该档位 extras 字面量的正则, 说明)
#
# 正则必须**锚定到具体档位分支**，不能只匹配 `EXTRAS="..."`：setup_*.sh 里
# minimal / standard / full 三个分支写法相同，只有分支标签能区分；构建脚本的
# Full 分支可能在 `then` 与赋值之间夹注释行，故允许 `_COMMENT_GAP`。
_COMMENT_GAP = r"(?:\s*\n\s*#[^\n]*)?"

PROFILE_SITES: dict[str, tuple[str, str]] = {
    "build-standard": (
        "build_linux.sh",
        r'else\s*\n\s*EXTRAS="([^"]+)"',
    ),
    "build-full-linux": (
        "build_linux.sh",
        r'if \[\[ "\$EDITION" == "Full" \]\]; then' + _COMMENT_GAP + r'\s*\n\s*EXTRAS="([^"]+)"',
    ),
    "build-full-macos": (
        "build_macos.sh",
        r'if \[\[ "\$EDITION" == "Full" \]\]; then' + _COMMENT_GAP + r'\s*\n\s*EXTRAS="([^"]+)"',
    ),
    "setup-linux-minimal": (
        "setup_linux.sh",
        r'minimal\)\s*\n\s*EXTRAS="([^"]+)"',
    ),
    "setup-linux-standard": (
        "setup_linux.sh",
        r'standard\)\s*\n\s*EXTRAS="([^"]+)"',
    ),
    "setup-linux-full": (
        "setup_linux.sh",
        r'full\)\s*\n\s*EXTRAS="([^"]+)"',
    ),
    "setup-macos-minimal": (
        "setup_macos.command",
        r'minimal\)\s*\n\s*EXTRAS="([^"]+)"',
    ),
    "setup-macos-standard": (
        "setup_macos.command",
        r'standard\)\s*\n\s*EXTRAS="([^"]+)"',
    ),
    "setup-macos-full": (
        "setup_macos.command",
        r'full\)\s*\n\s*EXTRAS="([^"]+)"',
    ),
    "install-windows-minimal": (
        "install_windows.ps1",
        r"if \(\$Minimal\) \{\s*\n\s*& \$Python -m pip install -e '\.\[([^\]]+)\]'",
    ),
    "install-windows-full": (
        "install_windows.ps1",
        r"\} else \{\s*\n\s*& \$Python -m pip install -e '\.\[([^\]]+)\]'",
    ),
    "docker": (
        "Dockerfile",
        r'ARG EXTRAS="([^"]+)"',
    ),
}


def load_profiles(path: Path = PROFILES_PATH) -> dict[str, str]:
    """读出 profile 名 → extras 字面量。"""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return {
        name: entry["extras"] for name, entry in data.get("profiles", {}).items()
    }


def declared_extras(root: Path) -> set[str]:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return set(data["project"]["optional-dependencies"])


def _profile_extras_are_known(extras: str, known: set[str]) -> list[str]:
    problems: list[str] = []
    names = [name.strip() for name in extras.split(",") if name.strip()]
    if not names:
        problems.append("empty extras list is not a pass")
        return problems
    for name in names:
        if name not in known:
            problems.append(f"unknown extra {name!r} (not in pyproject optional-dependencies)")
    return problems


def _site_mismatches(
    profiles: dict[str, str], sites: dict[str, tuple[str, str]], root: Path
) -> list[str]:
    problems: list[str] = []
    for name, extras in sorted(profiles.items()):
        site = sites.get(name)
        if site is None:
            problems.append(f"profile {name!r} is declared but has no known script site")
            continue
        rel, pattern = site
        path = root / rel
        if not path.is_file():
            problems.append(f"profile {name!r}: script {rel} is missing")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        match = re.search(pattern, text)
        if match is None:
            problems.append(
                f"profile {name!r}: could not locate its extras literal in {rel} "
                "(branch or wording changed?)"
            )
            continue
        found = match.group(1)
        if found != extras:
            problems.append(
                f"profile {name!r}: {rel} says {found!r} but constraints/profiles.toml "
                f"says {extras!r}"
            )
    for name in sorted(set(sites) - set(profiles)):
        problems.append(f"profile {name!r} has a script site but is not declared in profiles.toml")
    return problems


def check(root: Path = REPO_ROOT, profiles_path: Path | None = None) -> list[str]:
    path = profiles_path or root / "constraints" / "profiles.toml"
    if not path.is_file():
        return [f"profile declaration missing: {path}"]
    profiles = load_profiles(path)
    if not profiles:
        return ["empty selection is not a pass: no profiles declared in constraints/profiles.toml"]
    known = declared_extras(root)
    problems: list[str] = []
    for name, extras in sorted(profiles.items()):
        problems.extend(
            f"profile {name!r}: {problem}" for problem in _profile_extras_are_known(extras, known)
        )
    problems.extend(_site_mismatches(profiles, PROFILE_SITES, root))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(REPO_ROOT))
    args = parser.parse_args(argv)
    problems = check(Path(args.root))
    if problems:
        for problem in problems:
            print(f"ERROR  {problem}", file=sys.stderr)
        print(f"build profiles: {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print("build profiles: every install/build profile matches constraints/profiles.toml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
