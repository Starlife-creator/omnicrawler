"""组件端到端验证：真下载 → 真装配 → 真运行。

## 这个工具解决什么

2026-11-23 我加了 `convert-markdown` 组件，配方生成、门禁通过、`deps.lock`
也做了 —— 但**它到底能不能装、能不能跑，没有任何本地产物能回答**。

当时的唯一验证途径是等 CI 的 `portable-smoke`（约 25 分钟一轮，且它验的是
**便携包**而不是源码环境）。这正是我这两天反复踩的坑：**验证成本高，于是
不验证，于是把问题留给 CI**。

实测这条链路完全可以本地跑：

```
配方 → 真实下载 4 个轮子 → 装配 → entry.json 生成 → run_component
→ {"ok": true, "engine": "generic", "module": "markdownify"}
```

耗时约 1 分钟（纯 Python 组件），换来的是「这个组件真的能用」这个确定结论。

## 它做什么

对给定配方目录，依次：

1. `load_recipe` 读配方（结构校验在这里发生）
2. `install_from_recipe` + 真实 HTTP 下载器 → 装到临时目录
3. `build_wheelpack_spec` / `build_native_spec` 构造运行 spec
4. `run_component` 在**隔离子进程**里跑一次，核对 `smoke.expect`

任一步失败即判红，并指出失败在哪个阶段。

## 边界（如实说明）

* **需要联网**（从 PyPI / pytorch 等拉构件）。离线环境会失败，那不是组件的问题。
* **不做签名校验**：`install_from_recipe` 不强制签名（签名是 catalog 层的信任判据）。
  所以本工具能验证「配方正确且可运行」，但**验证不了「已签名入库」**。
* 大体积组件（torch 系列，数百 MB）会很慢 —— 用 `--timeout` 放宽或跳过。

## 用法

```powershell
python tools/market/verify_component_e2e.py --recipe ../OmniCrawler-market/components/convert/convert-markdown/recipe.json
python tools/market/verify_component_e2e.py --all      # 市场里全部配方
```
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import urllib.request
from pathlib import Path

OK_MARK = "[OK]"
BAD_MARK = "[FAIL]"

_ROOT = Path(__file__).resolve().parents[2]

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _download(url: str, dest: str) -> None:
    """真实 HTTP 下载（组件装配器要求的 downloader 形态）。"""
    with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 - 配方给的 https URL
        Path(dest).write_bytes(response.read())


def verify_one(recipe_path: Path, *, timeout: float) -> tuple[bool, str]:
    """验证单个配方。返回 `(是否通过, 说明)`。"""
    from omnicrawler.services.component_recipe import install_from_recipe, load_recipe

    try:
        recipe = load_recipe(recipe_path)
    except Exception as exc:  # noqa: BLE001 - 结构不合法要如实报
        return False, f"配方读取失败：{type(exc).__name__}: {exc}"

    artifacts = recipe.get("artifacts") or []
    total_mb = sum(int(a.get("size") or 0) for a in artifacts) / 1024 / 1024
    if total_mb > timeout:
        return False, (
            f"体积 {total_mb:.0f} MB 超过 --max-mb={timeout:.0f}，跳过"
            "（大包请显式放宽阈值）"
        )

    with tempfile.TemporaryDirectory(prefix="ocverify-") as tmp:
        work = Path(tmp)
        components_root = work / "components"
        components_root.mkdir()
        try:
            installed = install_from_recipe(
                recipe, components_root, downloader=_download, work_dir=work
            )
        except Exception as exc:  # noqa: BLE001 - 装配失败是主要失败面
            return False, f"装配失败：{type(exc).__name__}: {str(exc)[:160]}"

        component_dir = Path(installed["path"])
        kind = str(recipe.get("kind") or "wheel-pack").strip().lower()
        smoke = recipe.get("smoke") or {}

        # **纯数据组件没有 entry.json 是正常的**：`kind: data` 声明「装好即完成，
        # 无可执行入口」（如 tessdata）。早先一律要求 entry.json，把它们误判为失败。
        if kind == "data" or smoke.get("skip_run"):
            return True, (
                f"装配通过（kind={kind}，无运行阶段："
                f"{str(smoke.get('description') or '')[:40]}）"
            )

        if not (component_dir / "entry.json").is_file():
            return False, "装配后没有 entry.json"

        # 构造 spec 并运行。
        from omnicrawler.services import component_runner as runner
        try:
            if kind == "native":
                spec = runner.build_native_spec(component_dir)
            else:
                spec = runner.build_wheelpack_spec(component_dir)
        except Exception as exc:  # noqa: BLE001
            return False, f"构造运行 spec 失败：{type(exc).__name__}: {str(exc)[:160]}"

        smoke = recipe.get("smoke") or {}
        if smoke.get("skip_run"):
            return True, f"装配通过；smoke.skip_run=true（{smoke.get('description', '')[:40]}）"

        try:
            result = runner.run_component(spec, payload=smoke.get("payload") or {})
        except Exception as exc:  # noqa: BLE001 - 运行失败是核心失败面
            return False, f"运行失败：{type(exc).__name__}: {str(exc)[:200]}"

        if not isinstance(result, dict) or not result.get("ok"):
            return False, f"运行结果非 ok：{json.dumps(result, ensure_ascii=False)[:200]}"

        expected = tuple(smoke.get("expect") or ())
        missing = [key for key in expected if key not in result]
        if missing:
            return False, f"运行结果缺字段 {missing}（期望 {list(expected)}）"

        return True, f"装配+运行通过（engine={result.get('engine')}）"


def _iter_recipes(market: Path) -> list[Path]:
    components = market / "components"
    if not components.is_dir():
        return []
    return sorted(components.rglob("recipe.json"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="组件端到端验证：真下载 → 真装配 → 真运行",
    )
    parser.add_argument("--recipe", action="append", default=[], help="配方路径（可重复）")
    parser.add_argument("--all", action="store_true", help="验证市场里全部配方")
    parser.add_argument(
        "--market",
        default=str(_ROOT.parent / "OmniCrawler-market"),
        help="市场仓根（默认同级 OmniCrawler-market）",
    )
    parser.add_argument(
        "--max-mb",
        type=float,
        default=60.0,
        help="单个组件的构件总量上限（MB），超过则跳过。默认 60",
    )
    args = parser.parse_args(argv)

    targets: list[Path] = []
    if args.recipe:
        targets = [Path(item).resolve() for item in args.recipe]
    elif args.all:
        targets = _iter_recipes(Path(args.market))
    else:
        print(f"{BAD_MARK} 需要 --recipe 或 --all")
        return 2

    if not targets:
        print(f"{BAD_MARK} 没有找到任何配方（市场根：{args.market}）")
        return 1

    passed = 0
    failures: list[str] = []
    for recipe_path in targets:
        name = recipe_path.parent.name
        print(f"── {name} " + "─" * 40)
        ok, detail = verify_one(recipe_path, timeout=args.max_mb)
        if ok:
            passed += 1
            print(f"  {OK_MARK} {detail}")
        else:
            failures.append(f"{name}: {detail}")
            print(f"  {BAD_MARK} {detail}")

    print()
    print(f"共 {len(targets)} 个：通过 {passed}，失败 {len(failures)}")
    if failures:
        for item in failures:
            print(f"  - {item}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
