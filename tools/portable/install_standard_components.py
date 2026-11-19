"""把 Standard 便携包「按需装齐」到 Full 的能力水平（CI 用）。

## 为什么要这个模块

停发 Full 后对外只发 Standard。维护者的要求是：

> 假如说有人**全部安装并加载了**，那么和现在的 Full 版一模一样。

**Full 是「全装齐」的基准，Standard 天然不装齐** —— 这个方向不能搞反。
Standard 开箱即用（内置一份完整 Chromium + 轻量文档库），
需要 OCR / 重型能力时**按需装组件**。

所以等价性对账必须建立在「装齐之后」这个前提上。拿裸 Standard 去和 Full 比，
必然报一堆缺口 —— 那不是缺陷，是**判据用错了对象**。

## 做什么

给定一个已解压的 Standard 产物目录：

1. 找出 CLI 可执行文件；
2. 从市场快照 `market/catalog.json` 读出**全部组件条目**；
3. 逐个执行 `components install-recipe --recipe <market>/<recipe_file> --download`；
4. 装完再取能力报告（由调用方执行）。

## 边界（如实说明）

* **只装组件，不装插件**。插件的装载语义与组件不同（需要宿主 registry 参与），
  且 Full 的插件能力来自 `full` extra 而非组件。本模块只负责「组件能补上的那部分」。
* **失败不静默**：某个组件装不上就返回非 0 并在报告里点名，绝不"跳过当没看见"。
* **需要网络**：`--download` 会从上游拉构件（`ocr-paddle` 约 100 MB）。
  因此本模块只在 CI 里跑，本地静态门禁不调用它。
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


def _load_utf8_env():
    """按文件路径加载 `tools/child_env.py` 的 `utf8_env`。

    不能写 `from tools.child_env import utf8_env`：本脚本在 CI 里是被
    `python tools/portable/install_standard_components.py` 直接执行的，
    此时 `sys.path[0]` 是 `tools/portable/`，仓库根不在路径上
    ⇒ `ModuleNotFoundError`（v1.0.5 在收集脚本上踩过同一个坑）。
    """
    import importlib.util as _ilu

    root = Path(__file__).resolve().parents[2]
    spec = _ilu.spec_from_file_location("_child_env", root / "tools" / "child_env.py")
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ImportError(f"无法加载 {root / 'tools' / 'child_env.py'}")
    module = _ilu.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.utf8_env


_utf8_env = _load_utf8_env()


@dataclass
class InstallReport:
    """装齐过程的结果。"""

    installed: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed

    def summary(self) -> str:
        parts = [f"已装 {len(self.installed)} 个组件"]
        if self.failed:
            parts.append(f"失败 {len(self.failed)} 个")
        if self.skipped:
            parts.append(f"跳过 {len(self.skipped)} 个")
        return "，".join(parts)


def load_component_entries(catalog_path: Path) -> list[dict]:
    """从市场 catalog 读出全部组件条目（按 id 排序，保证顺序确定）。"""
    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    components = data.get("components") or []
    if not isinstance(components, list):
        raise ValueError(f"{catalog_path} 的 components 不是数组")
    entries = [item for item in components if isinstance(item, dict) and item.get("id")]
    return sorted(entries, key=lambda item: str(item.get("id")))


def _recipe_path_for(market_root: Path, entry: dict) -> Path:
    """组件条目的 recipe 文件路径。

    catalog 里的 `recipe_file` 形如 `components/ocr-paddle/recipe.json`，
    相对市场仓根。兼容只有 `id` 的旧条目。
    """
    rel = str(entry.get("recipe_file") or "").strip()
    if not rel:
        rel = f"components/{entry['id']}/recipe.json"
    return market_root / rel


def install_all_components(
    executable: Path,
    release_root: Path,
    market_root: Path,
    *,
    timeout: float = 900.0,
) -> InstallReport:
    """把市场里的组件逐个装到 `release_root` 下。"""
    report = InstallReport()
    catalog = market_root / "catalog.json"
    if not catalog.is_file():
        report.failed.append(("<catalog>", f"市场快照不存在：{catalog}"))
        return report

    try:
        entries = load_component_entries(catalog)
    except (OSError, ValueError) as exc:
        report.failed.append(("<catalog>", str(exc)))
        return report

    if not entries:
        # 空集合会让"装齐"变成恒真 —— 必须显式指出
        report.failed.append(("<catalog>", "市场快照里没有任何组件条目"))
        return report

    for entry in entries:
        cid = str(entry["id"])
        recipe = _recipe_path_for(market_root, entry)
        if not recipe.is_file():
            report.skipped.append((cid, f"recipe 缺失：{recipe}"))
            continue
        completed = subprocess.run(  # noqa: S603 - argv 由本模块构造
            [
                str(executable),
                "components",
                "install-recipe",
                "--recipe",
                str(recipe),
                "--download",
            ],
            cwd=str(release_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_utf8_env(),
            check=False,
        )
        if completed.returncode == 0:
            report.installed.append(cid)
        else:
            detail = (completed.stderr or completed.stdout or "").strip()[:400]
            report.failed.append((cid, detail or f"退出码 {completed.returncode}"))
    return report


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 3:
        print("用法: install_standard_components.py <release_root> <market_root> <edition_label>")
        return 2
    release_root = Path(args[0])
    market_root = Path(args[1])
    label = args[2]

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from collect_capability_reports import locate_executable  # noqa: PLC0415

    executable = locate_executable(release_root)
    if executable is None:
        print(f"::error::{label} 产物里没找到可执行文件，无法装齐")
        return 3

    report = install_all_components(executable, release_root, market_root)
    print(f"{label} 组件装齐：{report.summary()}")
    for cid in report.installed:
        print(f"  ✓ {cid}")
    for cid, why in report.skipped:
        print(f"::warning::{label} 跳过组件 {cid}：{why}")
    for cid, why in report.failed:
        print(f"::error::{label} 组件 {cid} 安装失败：{why}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
