"""反向索引：`tools/` 下的可执行脚本必须至少被一处引用，否则判红。

## 这个门禁解决什么

本会话我新增了 `check_test_cleanliness.py` 与 `reverse_assert.py`，
位置正确、逻辑正确、也能跑 —— 但**没有任何地方调用它们**：

| 工具 | 被引用处数 |
|---|---|
| `offline_acceptance`（既有） | 4 |
| `ci_coverage_pytest`（既有） | 4 |
| **`check_test_cleanliness`（我新增）** | **0** |
| **`reverse_assert`（我新增）** | **0** |

「写了就忘」与「放错位置」一样是白做。而这件事完全可以机械化判定：
**一个没有调用者的工具，等于不存在。**

## 判据

对 `tools/` 下每个**可执行脚本**（有 `__main__` 且声明了 CLI 入口），
要求它至少被下面任一处引用：

1. `tools/gate_registry.py` —— 登记为门禁（本地 `self_verify` 会跑）
2. `.github/workflows/*.yml` —— CI 直接调用
3. 文档（`AGENTS.md` / `README.md` / `docs/*.md`）—— 写明了用法

三者都没有，就判红。

## 为什么区分「可执行脚本」与「纯库」

`tools/child_env.py`、`tools/builders/manifest_common.py` 这类是被 import 的
库，没有 CLI，不该要求「有人调用」。判据用「有 `__main__` **且** 有
argparse/sys.argv」来识别真正的入口脚本。

## 豁免

有些脚本是**一次性/历史性**的（如 `ressign_market.py` 是某次批量重签用过的），
保留价值但不该被日常引用。这类登记在 `_HISTORICAL` 里并写清理由 ——
显式豁免优于让它悄悄漂着。

## 用法

```powershell
python tools/gates/check_tool_wiring.py
```
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

OK_MARK = "[OK]"
BAD_MARK = "[FAIL]"

_ROOT = Path(__file__).resolve().parents[2]

#: 显式豁免：一次性/历史性脚本，保留但不要求被引用。
#: 键是相对仓库根的路径，值是「为什么可以不被调用」。
_HISTORICAL = {
    "tools/signing/ressign_market.py": "某次批量重签的一次性脚本，已完成使命",
    "tools/qa/reorganize_tests.py": "一次性重组测试目录，已完成使命",
    "tools/benchmarks/measure_optimization_baseline.py": "取基线的临时工具，按需手工调用",
    "tools/market/gen_component_recipe.py": (
        "由 tools/market/new_component.py 内部调用（不是直接命令行入口）"
    ),
    "tools/signing/identity.py": "身份管理辅助脚本，按需手工调用（文档见签名一页纸）",
    "tools/signing/sign_plugins_batch.py": "批量签名辅助，按需手工调用",
    "tools/portable/install_standard_components.py": (
        "已被 verify_standard_components.py 取代（后者把「装齐」与「验证」合并）；"
        "保留作为独立安装口的参考实现"
    ),
}

#: 文档里算作「写明了用法」的来源。
_DOC_SOURCES = ("AGENTS.md", "README.md")


def _is_executable_script(text: str) -> bool:
    """有 `__main__` 且声明了 CLI 入口，才算可执行脚本。"""
    if "__main__" not in text:
        return False
    return "argparse" in text or "sys.argv" in text


def _collect_references(root: Path) -> tuple[set[str], dict[str, int]]:
    """收集三处引用。返回 `(被引用的路径集合, 各处计数)`。"""
    referenced: set[str] = set()
    stats = {"registry": 0, "ci": 0, "docs": 0}

    # ① gate_registry
    registry = root / "tools" / "gate_registry.py"
    if registry.is_file():
        found = set(re.findall(r'"(tools/[\w/]+\.py)"', registry.read_text(encoding="utf-8")))
        referenced |= found
        stats["registry"] = len(found)

    # ② CI workflows
    #
    # ★ **只算实际执行**，不算注释里的提及。
    # `release.yml` 的注释里提到 `portable_smoke_test.py` 是在解释「为什么
    # 构建期检查不够」，并不调用它 —— 把注释算进来会把「没接线」误判成「已接线」，
    # 那正是本门禁要防的事。
    for workflow in (root / ".github" / "workflows").glob("*.yml"):
        text = workflow.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            found = set(re.findall(r"(tools/[\w/]+\.py)", line))
            referenced |= found
            stats["ci"] += len(found)

    # ③ 文档（仓内 + **工作区根**）
    #
    # ★ 为什么要看工作区根：`sign_component.py` / `sync_snapshot.py` 这类脚本
    # 的用法写在 `../发布与维护流程.md`、`../签名一页纸.md` 里 —— 那是
    # 跨两个仓库的治理文档，只能放根目录（根目录无版本控制，所以 CI 看不到）。
    # 不包含它们会把「明明有文档」的脚本误判为「没人调用」。
    doc_files = [root / name for name in _DOC_SOURCES]
    doc_files += sorted((root / "docs").glob("*.md"))
    world_root = root.parent
    doc_files += sorted(world_root.glob("*.md")) if world_root != root else []
    for doc in doc_files:
        if not doc.is_file():
            continue
        # 文档里可能是正斜杠也可能是**反斜杠**（PowerShell 命令习惯）：
        # `tools\signing\sign_component.py` 与 `tools/signing/sign_component.py`
        # 必须都认，否则「明明有 20 处引用」的脚本会被误判没人用。
        text = doc.read_text(encoding="utf-8").replace("\\", "/")
        found = set(re.findall(r"(tools/[\w/]+\.py)", text))
        # 文档也常写**裸文件名**（`sign_update_manifest.py`），尤其在表格里。
        # 只认完整路径会把这类误判成「没人调用」。
        bare = set(re.findall(r"([\w.-]+\.py)", text))
        referenced |= found
        # 裸名要映射回路径，否则与 `tools/...` 形式对不上；
        # 交给调用方按文件名匹配（见 find_unwired）。
        referenced |= {f"<bare>/{name}" for name in bare}
        stats["docs"] += len(found)

    return referenced, stats


def _collect_internal_callers(root: Path) -> set[str]:
    """收集「被 `tools/` 或 `src/` 下其它模块 import 或调用」的脚本。

    ★ 这是一处重要来源：`tools/builders/create_zip.py` 由
    `tools/builders/__init__.py` 导出、「`diffable_delivery`」等模块调用。
    它**没有 CLI 调用者**，但是活的 —— 只看 CI/门禁/文档会把它误判为死代码。

    判据用「文件名词干出现在其它 .py 里」，足够精确：
    脚本名通常足够独特（`create_zip` / `compile_mo` / `generate_release_info`）。
    """
    called: set[str] = set()
    own_files: dict[str, Path] = {}
    for base in ("tools", "src"):
        for path in (root / base).rglob("*.py"):
            own_files[str(path)] = path

    # 收集所有源码文本（排除脚本自身）
    texts: dict[str, str] = {}
    for name, path in own_files.items():
        try:
            texts[name] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

    for name, path in own_files.items():
        if path.name in {"__init__.py", "__main__.py"}:
            continue
        stem = path.stem
        if len(stem) < 6:  # 太短的名字容易误匹配
            continue
        for other_name, other_text in texts.items():
            if other_name == name:
                continue
            if stem in other_text:
                called.add(path.relative_to(root).as_posix())
                break
    return called


def find_unwired(root: Path) -> tuple[list[str], int]:
    """返回 `(没人调用的脚本, 检查的脚本总数)`。"""
    referenced, _stats = _collect_references(root)
    referenced |= _collect_internal_callers(root)
    unwired: list[str] = []
    total = 0
    for path in sorted((root / "tools").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        if relative in _HISTORICAL:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not _is_executable_script(text):
            continue
        total += 1
        if relative not in referenced and f"<bare>/{path.name}" not in referenced:
            unwired.append(relative)
    return unwired, total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="检查 tools/ 下的可执行脚本是否都有调用者",
    )
    parser.add_argument("--root", default="", help="仓库根（缺省＝本文件上两级）")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else _ROOT
    unwired, total = find_unwired(root)

    # **防空集恒真**：一个都没扫到说明识别逻辑失效了。
    if total < 20:
        print(
            f"{BAD_MARK} 只识别出 {total} 个可执行脚本 —— "
            "判据可能已失效（空集会让断言恒真）"
        )
        return 1

    _referenced, stats = _collect_references(root)

    if unwired:
        print(f"{BAD_MARK} 这些脚本没有任何调用者（写了就忘等同不存在）：")
        for item in unwired:
            print(f"  - {item}")
        print(
            "\n三种接入方式任选其一：\n"
            "  1. 登记到 tools/gate_registry.py（本地 self_verify 会跑）\n"
            "  2. 在 .github/workflows/*.yml 里调用（CI 会跑）\n"
            "  3. 写进 AGENTS.md / README.md / docs/*.md（说明怎么用）\n"
            "若是一次性/历史性脚本，登记到本文件的 _HISTORICAL 并写清理由。"
        )
        return 1

    print(
        f"{OK_MARK} tools/ 下 {total} 个可执行脚本都有调用者"
        f"（引用来源：门禁{stats['registry']} / CI{stats['ci']} / 文档{stats['docs']}）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
