"""判断本轮改动是否触及**构建链**，决定能否复用上次构建产物。

## 为什么需要这个判断

`component-probe` 想复用上次 release 的 Standard 产物（省掉 40 分钟三平台构建）。
但这是有前提的：**复用旧产物只能验证「运行期」的变化**。

如果本轮改的是**构建链本身**（PyInstaller spec、打包脚本、运行时准备、
冻结入口、`runtime_paths`），那么旧产物里**根本不含这些改动** ——
复用它等于什么都没测，还会给出「通过」的假象。

维护者 2026-11-20 指出过这一点：

> 比如说我们这次优化了构建，那你直接复用上次构建产物，岂不是就测试不了
> 你这次的结果了吗？

所以本脚本输出一个结论，供 workflow 决策：

* `build` —— 必须重新构建（改动触及构建链，或无法判断）
* `reuse` —— 可以复用上次产物（改动只在组件/配方/运行期服务层）

## 保守原则

**判断不了就重建。** 宁可多花 40 分钟，也不要给出假的通过。
"""

from __future__ import annotations

import argparse
import subprocess
import sys

#: 触及这些路径 ⇒ 必须重建（构建产物会变）。
BUILD_CHAIN_PATTERNS: tuple[str, ...] = (
    "packaging/",
    "build_windows.ps1",
    "build_linux.sh",
    "build_macos.sh",
    "install_windows.ps1",
    "setup_linux.sh",
    "setup_macos.command",
    "constraints/",
    "pyproject.toml",
    "uv.lock",
    "tools/prepare_windows_runtime.ps1",
    "tools/prepare_linux_runtime.sh",
    "tools/prepare_macos_runtime.sh",
    "tools/builders/",
    "tools/portable/prepare_",
    "src/omnicrawler/core/runtime_paths.py",
    "assets/branding/",
)

#: 这些改动**不影响**构建产物，可以复用（运行期/组件层）。
RUNTIME_ONLY_PATTERNS: tuple[str, ...] = (
    "src/omnicrawler/services/component_",
    "src/omnicrawler/services/component_runner.py",
    "src/omnicrawler/services/component_worker.py",
    "src/omnicrawler/services/component_smoke.py",
    "src/omnicrawler/services/component_recipe.py",
    "market/components/",
    "tools/portable/verify_standard_components.py",
    "tools/gates/check_component_recipes.py",
    "tools/market/component_deps_lock.py",
    "tests/",
    "docs/",
)


def classify(paths: list[str]) -> tuple[str, str, list[str]]:
    """返回 `(决策, 理由, 触发重建的路径)`。

    决策是 `build` 或 `reuse`；无法判断时返回 `build`（保守）。
    """
    normalized = [p.replace("\\", "/").strip() for p in paths if p.strip()]
    if not normalized:
        return "build", "改动列表为空 —— 无法判断，保守重建", []

    build_hits = [
        p for p in normalized if any(p.startswith(pat) or p == pat for pat in BUILD_CHAIN_PATTERNS)
    ]
    if build_hits:
        return (
            "build",
            f"改动触及构建链（{len(build_hits)} 个文件）—— 旧产物不含这些改动，"
            "复用它等于没测",
            build_hits,
        )

    unknown = [
        p
        for p in normalized
        if not any(p.startswith(pat) for pat in RUNTIME_ONLY_PATTERNS)
    ]
    if unknown:
        return (
            "build",
            f"有 {len(unknown)} 个改动文件无法归类 —— 保守重建",
            unknown,
        )

    return "reuse", "改动只在运行期/组件层 —— 构建产物未变，可复用上次产物", []


def changed_files(base: str, head: str) -> list[str]:
    """列出 base..head 之间的改动文件。"""
    completed = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        return []
    return [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="判断是否必须重建构建产物")
    parser.add_argument("--base", default="", help="对比基线（缺省用 HEAD~1）")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--paths", nargs="*", default=None, help="直接给路径列表（跳过 git）")
    parser.add_argument(
        "--github-output",
        action="store_true",
        help="按 GitHub Actions 格式输出 decision=build|reuse",
    )
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    if args.paths is not None:
        paths = list(args.paths)
    else:
        base = args.base or "HEAD~1"
        paths = changed_files(base, args.head)

    decision, reason, triggers = classify(paths)

    if args.github_output:
        print(f"decision={decision}")
        print(f"reason={reason}")
    else:
        print(f"决策：{decision}")
        print(f"理由：{reason}")
        if triggers:
            print("触发重建的路径：")
            for item in triggers[:20]:
                print(f"  - {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
