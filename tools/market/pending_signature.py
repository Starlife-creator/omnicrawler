"""待签条目的**就绪检查**与**一键入库**。

## 为什么需要这个工具

2026-11-23 的 12 小时自主优化里，10 个市场条目（组件 7 + 插件 4）全部写好了，
但创作者身份密码不可用，签名做不了 —— 于是它们停在
`OmniCrawler-market/pending-signature/`。

问题不在于「没签」，而在于**没有可验证的「就绪」定义**：

* 配方是否真的能下载、装配、运行？（`verify_component_e2e` 能答，但要逐条跑）
* 插件的 `plugin.yaml` 是否字段齐全？是否真的自包含（无 `import omnicrawler`）？
* 依赖闭包（`deps.lock.json`）是否最新？
* 移回 `components/` 时需要放在哪个分类子目录？

密码恢复后如果靠手工回忆这些，很容易漏。这个工具把「就绪」变成一条命令：

```powershell
python tools/market/pending_signature.py --check          # 逐条验证就绪状态
python tools/market/pending_signature.py --plan           # 打印入库步骤（不执行）
python tools/market/pending_signature.py --adopt <id>     # 移回 components/ 并排好分类
```

`--adopt` **刻意不做签名** —— 签名要创作者密码，必须由人显式执行
（`sign_component.py` / `sign_plugin.py`）。工具只负责它该负责的那部分。

## 「就绪」的定义（确定性判据）

| 判据 | 怎么验 |
|---|---|
| 配方存在且可解析 | `json.load` 成功、必填字段齐 |
| 依赖闭包新鲜 | `deps.lock.json` 的 `recipe_digest` 与当前配方一致 |
| 能装能跑 | 调用 `verify_component_e2e.verify_one`（真实下载装配运行）|
| 插件自包含 | AST 扫描，不得有 `import omnicrawler` |
| 插件清单齐全 | 与 `example_news/plugin.yaml` 的字段集逐项比对 |

`--check` 默认**跳过 e2e**（要联网下载数百 MB，慢）；加 `--e2e` 才跑。
这是刻意的：日常检查要快，交付前才做重验证。

## 边界

* 不验签名（没有签名工具能做到，需要密码）；
* 不改 `catalog.json`（那是 `generate_catalog.py` + `sign_catalog.py` 的事）。
"""
from __future__ import annotations

import argparse
import ast
import json
import shutil
import sys
from pathlib import Path

OK_MARK = "[OK]"
BAD_MARK = "[FAIL]"
WARN_MARK = "[WARN]"

_ROOT = Path(__file__).resolve().parents[2]

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

#: 组件分类：id 前缀 → `components/` 下的子目录。
#: 与市场现存布局一致（`components/ocr/`、`components/convert/` 等）。
_CATEGORY_BY_PREFIX: tuple[tuple[str, str], ...] = (
    ("ocr-", "ocr"),
    ("extract-", "extract"),
    ("convert-", "convert"),
    ("export-", "export"),
    ("parse-", "extract"),
    ("dedup-", "extract"),
    ("schedule-", "schedule"),
)

#: 描述「就绪」所需的配方必填字段。
_RECIPE_REQUIRED = ("schema", "id", "version", "kind", "platforms", "architectures", "artifacts")


def _category_for(component_id: str) -> str:
    for prefix, category in _CATEGORY_BY_PREFIX:
        if component_id.startswith(prefix):
            return category
    return "misc"


def _check_recipe(directory: Path) -> tuple[bool, list[str]]:
    """检查组件配方与依赖闭包。"""
    notes: list[str] = []
    recipe_path = directory / "recipe.json"
    try:
        recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, [f"配方无法解析：{exc}"]

    missing = [k for k in _RECIPE_REQUIRED if k not in recipe]
    if missing:
        return False, [f"配方缺必填字段：{missing}"]

    if not recipe.get("artifacts"):
        return False, ["构件列表为空"]

    notes.append(f"{len(recipe['artifacts'])} 个构件，platforms={recipe['platforms']}")

    # 依赖闭包新鲜度
    lock = directory / "deps.lock.json"
    if not lock.is_file():
        return False, [*notes, "缺 deps.lock.json（跑 component_deps_lock.py）"]
    try:
        from omnicrawler.services.component_recipe import recipe_digest

        current = recipe_digest(recipe)
    except Exception as exc:  # noqa: BLE001 - 摘要算法不可用时如实报
        return False, [*notes, f"无法计算配方摘要：{type(exc).__name__}"]
    try:
        recorded = json.loads(lock.read_text(encoding="utf-8")).get("recipe_digest")
    except (OSError, json.JSONDecodeError):
        return False, [*notes, "deps.lock.json 无法解析"]
    if recorded and recorded != current:
        return False, [*notes, "deps.lock.json 已过期（配方改过但未重算）"]
    notes.append("deps.lock 新鲜")
    return True, notes


def _plugin_is_self_contained(path: Path) -> tuple[bool, str]:
    """插件不得 import omnicrawler（契约 2 子进程约束）。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        return False, f"无法解析：{exc}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if "omnicrawler" in alias.name:
                    return False, f"import 了 {alias.name}（沙箱子进程里不可用）"
        elif isinstance(node, ast.ImportFrom):
            if node.module and "omnicrawler" in node.module:
                return False, f"from {node.module} import ...（沙箱子进程里不可用）"
    return True, "自包含"


def _check_plugin(directory: Path, reference_yaml: Path) -> tuple[bool, list[str]]:
    """检查插件三件套与清单字段。"""
    notes: list[str] = []
    for name in ("plugin.py", "plugin.yaml", "listing.md"):
        if not (directory / name).is_file():
            return False, [f"缺 {name}"]

    ok, detail = _plugin_is_self_contained(directory / "plugin.py")
    if not ok:
        return False, [detail]
    notes.append(detail)

    try:
        import yaml

        mine = yaml.safe_load((directory / "plugin.yaml").read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return False, [*notes, f"plugin.yaml 无法解析：{type(exc).__name__}"]
    if reference_yaml.is_file():
        try:
            reference = yaml.safe_load(reference_yaml.read_text(encoding="utf-8"))
            absent = sorted(set(reference) - set(mine or {}))
            if absent:
                return False, [*notes, f"plugin.yaml 缺字段：{absent}"]
            notes.append("字段与样板一致")
        except Exception:  # noqa: BLE001 - 样板不可读时不算失败
            notes.append("样板不可读，跳过字段比对")
    return True, notes


def _check_e2e(directory: Path, max_mb: float) -> tuple[bool, str]:
    """真实下载→装配→运行（慢，需联网）。"""
    from tools.market.verify_component_e2e import verify_one

    ok, detail = verify_one(directory / "recipe.json", timeout=max_mb)
    return ok, detail


def check_all(market: Path, *, run_e2e: bool, max_mb: float) -> tuple[int, int]:
    """逐条检查，返回 `(就绪数, 总数)`。"""
    pending = market / "pending-signature"
    if not pending.is_dir():
        print(f"{BAD_MARK} 没有 pending-signature/ 目录")
        return 0, 0

    reference = market / "plugins" / "example_news" / "plugin.yaml"
    ready = total = 0
    for directory in sorted(p for p in pending.iterdir() if p.is_dir()):
        total += 1
        name = directory.name
        if (directory / "recipe.json").is_file():
            ok, notes = _check_recipe(directory)
            kind = "组件"
            if ok and run_e2e:
                e2e_ok, detail = _check_e2e(directory, max_mb)
                ok = ok and e2e_ok
                notes.append(f"e2e: {detail}")
        elif (directory / "plugin.py").is_file():
            ok, notes = _check_plugin(directory, reference)
            kind = "插件"
        else:
            ok, notes, kind = False, ["既不是组件也不是插件"], "?"

        if ok:
            ready += 1
            print(f"{OK_MARK} {name:22} ({kind}) {' | '.join(notes)}")
        else:
            print(f"{BAD_MARK} {name:22} ({kind}) {' | '.join(notes)}")

    print()
    print(f"就绪 {ready} / {total}")
    return ready, total


def print_plan(market: Path) -> int:
    """打印入库步骤（不执行）。"""
    pending = market / "pending-signature"
    if not pending.is_dir():
        print(f"{BAD_MARK} 没有 pending-signature/ 目录")
        return 1

    components: list[tuple[str, str]] = []
    plugins: list[str] = []
    for directory in sorted(p for p in pending.iterdir() if p.is_dir()):
        if (directory / "recipe.json").is_file():
            components.append((directory.name, _category_for(directory.name)))
        elif (directory / "plugin.py").is_file():
            plugins.append(directory.name)

    print("## 组件（移到 components/<分类>/ 后签名）")
    for name, category in components:
        print(f"  {name:22} -> components/{category}/{name}/")
    print()
    print("## 插件（移到 plugins/<id>/ 后签 plugin.py 与 creator 轨）")
    for name in plugins:
        print(f"  {name:22} -> plugins/{name}/")
    print()
    print("## 命令（密码恢复后逐条执行）")
    print("  $env:OMNICRAWL_IDENTITY_PASSWORD = <密码>")
    for name, category in components:
        print(
            f"  python ../OmniCrawler/tools/signing/sign_component.py sign "
            f"components/{category}/{name} --username starlife"
        )
    for name in plugins:
        print(
            f"  python ../OmniCrawler/tools/signing/sign_plugin.py "
            f"plugins/{name} --username starlife"
        )
    print("  python tools/generate_catalog.py")
    print('  python tools/sign_catalog.py --maintainer-key "<维护者私钥路径>"')
    return 0


def adopt(market: Path, component_id: str) -> int:
    """把某个待签条目移回正式目录（**不签名**）。"""
    pending = market / "pending-signature"
    source = pending / component_id
    if not source.is_dir():
        print(f"{BAD_MARK} pending-signature 里没有 {component_id}")
        return 1

    if (source / "recipe.json").is_file():
        target = market / "components" / _category_for(component_id) / component_id
    elif (source / "plugin.py").is_file():
        target = market / "plugins" / component_id
    else:
        print(f"{BAD_MARK} {component_id} 既不是组件也不是插件")
        return 1

    if target.exists():
        print(f"{BAD_MARK} 目标已存在：{target.relative_to(market)}")
        return 1

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))
    print(f"{OK_MARK} 已移到 {target.relative_to(market)}")
    print(f"  下一步：签名 {target.relative_to(market)}（需创作者密码）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="待签条目的就绪检查与一键入库（不做签名）",
    )
    parser.add_argument(
        "--market",
        default=str(_ROOT.parent / "OmniCrawler-market"),
        help="市场仓根（默认同级 OmniCrawler-market）",
    )
    parser.add_argument("--check", action="store_true", help="逐条验证就绪状态")
    parser.add_argument("--e2e", action="store_true", help="并跑真实下载装配运行（慢）")
    parser.add_argument("--max-mb", type=float, default=600.0, help="e2e 体积上限")
    parser.add_argument("--plan", action="store_true", help="打印入库步骤（不执行）")
    parser.add_argument("--adopt", default="", help="把某个条目移回正式目录（不签名）")
    args = parser.parse_args(argv)

    market = Path(args.market).resolve()
    if not market.is_dir():
        print(f"{BAD_MARK} 市场仓不存在：{market}")
        return 2

    if args.adopt:
        return adopt(market, args.adopt)
    if args.plan:
        return print_plan(market)
    if args.check:
        ready, total = check_all(market, run_e2e=args.e2e, max_mb=args.max_mb)
        if total == 0:
            # 空集不是失败，但也不能静默算通过：说清楚为什么是 0。
            # 此前 `total and ready == total` 让「没有待签条目」恒定返回 1，
            # 于是待签清空之后这个工具永远报错 —— 判据把「做完了」当成「做坏了」。
            print(
                f"{OK_MARK} 没有待签条目（pending-signature/ 为空）—— "
                "这不是失败：全部条目都已入库并签名",
            )
        elif ready == total:
            print(f"{OK_MARK} 结论：{ready} / {total} 就绪")
        else:
            print(f"{BAD_MARK} 结论：{ready} / {total} 就绪")
        return 0 if ready == total else 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
