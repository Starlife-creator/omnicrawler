"""新增组件的**一条龙**：把 9 步手工流程收成一条命令。

## 痛点：顺序错就得重来

新增一个 Wheel-Pack 组件原本要手工跑 9 步：

```
1. gen_component_recipe.py     # 生成配方
2. component_deps_lock.py      # 生成闭包快照
3. sign_component.py sign      # 创作者签名（要密码）
4. sign_component.py maintainer-sign
5. generate_catalog.py
6. sign_catalog.py
7. check_component_recipes.py  # 校验
8. sync_snapshot.py            # 同步到主仓
9. git commit ×2（市场仓 + 主仓）
```

**我犯过的顺序错误**（每步都能让前面白做）：

* 先签名后改配方 ⇒ 签名失效，必须重签
* 改配方忘重跑 `deps_lock` ⇒ 门禁报「已过期」
* 改了配方忘重签 ⇒ 门禁报「清单与文件不一致」
* 重签时旧维护者签名还在 ⇒ 自检失败

## 这个工具做什么

把**无密码的前半段**自动化，并在每步之间做前置校验：

```
gen_component_recipe  →  deps_lock  →  check_component_recipes
```

签名单独留一手（要密码，且市场仓还需要维护者签名），但会打印
**精确的后续命令**，包括当前配方文件的 sha256 —— 改配方后能立刻看出
签名是否已过期。

## 为什么签名不自动化

创作者密码不该出现在命令行参数或环境变量里由工具自行读取。
那会诱使把密码写进脚本。签名保持人工触发是**刻意的安全边界**。

## 用法

```powershell
python tools/market/new_component.py `
    --package rapidocr --id ocr-rapidocr --module rapidocr `
    --description "RapidOCR：ONNX 运行时" --also-platforms linux

# 只演算不落盘
python tools/market/new_component.py ... --dry-run
```
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

OK_MARK = "[OK]"
BAD_MARK = "[FAIL]"

_ROOT = Path(__file__).resolve().parents[2]

# ★ 直接运行本脚本时 `sys.path[0]` 是 `tools/market/`，不是仓库根。
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _python() -> str:
    exe = _ROOT / ".venv" / "Scripts" / "python.exe"
    return str(exe) if exe.is_file() else sys.executable


def _run(argv: list[str], *, dry_run: bool) -> int:
    """跑一条命令；`dry_run` 时只打印。"""
    printable = " ".join(argv)
    print(f"  $ {printable}")
    if dry_run:
        return 0
    from tools.child_env import utf8_env

    result = subprocess.run(
        argv,
        cwd=_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=utf8_env(),
        check=False,
    )
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="新增组件的一条龙：配方 → 闭包 → 校验（签名另跑）",
    )
    parser.add_argument("--package", required=True, help="根包名（如 rapidocr）")
    parser.add_argument("--id", required=True, help="组件 id（等于目录名）")
    parser.add_argument("--module", required=True, help="入口模块（如 rapidocr）")
    parser.add_argument("--description", default="", help="组件描述")
    parser.add_argument("--engine", default="generic", help="探测引擎")
    parser.add_argument("--platform", default="windows", help="主平台")
    parser.add_argument(
        "--also-platforms",
        default="",
        help="额外平台（逗号分隔，如 linux）。**CI 跑 Linux，不给就会装配失败**",
    )
    parser.add_argument(
        "--category",
        default="ocr",
        help="分类子目录名（组件路径为 components/<分类>/<id>）",
    )
    parser.add_argument(
        "--market",
        default="",
        help="市场仓路径（缺省＝主仓的同级 OmniCrawler-market）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只演算，不落盘")
    args = parser.parse_args(argv)

    market = Path(args.market) if args.market else _ROOT.parent / "OmniCrawler-market"
    if not market.is_dir():
        print(f"{BAD_MARK} 市场仓不存在：{market}")
        return 2

    component_dir = market / "components" / args.category / args.id
    recipe = component_dir / "recipe.json"

    print(f"组件目录：{component_dir}")
    print(f"配方文件：{recipe}")
    print()

    python = _python()

    # ── 第 1 步：生成配方 ────────────────────────────────────────────────
    print("[1/3] 生成配方（从 PyPI 解析完整依赖闭包，需联网）")
    gen_argv = [
        python,
        str(_ROOT / "tools" / "market" / "gen_component_recipe.py"),
        args.package,
        "--id", args.id,
        "--module", args.module,
        "--engine", args.engine,
        "--platform", args.platform,
        "--out", str(component_dir),
    ]
    if args.description:
        gen_argv += ["--description", args.description]
    if args.also_platforms:
        gen_argv += ["--also-platforms", args.also_platforms]
    if _run(gen_argv, dry_run=args.dry_run) != 0:
        print(f"{BAD_MARK} 生成配方失败")
        return 1

    # ── 第 2 步：闭包快照 ────────────────────────────────────────────────
    print("\n[2/3] 生成依赖闭包快照（deps.lock.json）")
    if _run(
        [
            python,
            str(_ROOT / "tools" / "market" / "component_deps_lock.py"),
            "--recipe", str(recipe),
        ],
        dry_run=args.dry_run,
    ) != 0:
        print(f"{BAD_MARK} 生成闭包快照失败")
        return 1

    # ── 第 3 步：校验 ────────────────────────────────────────────────────
    print("\n[3/3] 校验配方声明可从构件字节推导")
    if _run(
        [
            python,
            str(_ROOT / "tools" / "gates" / "check_component_recipes.py"),
            "--root", str(market / "components"),
        ],
        dry_run=args.dry_run,
    ) != 0:
        print(f"{BAD_MARK} 配方校验失败")
        return 1

    if args.dry_run:
        print(f"\n{OK_MARK} 演算完成（未落盘）")
        return 0

    # ── 后续：签名与提交（人工，因为要密码） ─────────────────────────────
    import hashlib

    recipe_sha = hashlib.sha256(recipe.read_bytes()).hexdigest()
    print(f"\n{OK_MARK} 配方已就绪，sha256 = {recipe_sha[:16]}…")
    print("\n接下来**手动**执行（签名需要创作者密码，刻意不自动化）：\n")
    print("  # ① 创作者签名（要密码）")
    print("  $sec = Read-Host -AsSecureString '创作者身份密码'")
    print(
        "  $env:OMNICRAWL_IDENTITY_PASSWORD = "
        "[Runtime.InteropServices.Marshal]::PtrToStringAuto("
        "[Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))"
    )
    print(
        f"  python tools\\signing\\sign_component.py sign "
        f"\"{component_dir}\" --username starlife"
    )
    print("\n  # ② 维护者签名（不需要密码）")
    print(
        f"  python tools\\signing\\sign_component.py maintainer-sign "
        f"\"{component_dir}\""
    )
    print("\n  # ③ 重建并签名 catalog")
    print(f"  cd \"{market}\"")
    print("  python tools\\generate_catalog.py")
    print(
        "  python tools\\sign_catalog.py --maintainer-key "
        "\"C:\\Users\\Lenovo\\Desktop\\档案\\plugin_signing_private.pem\""
    )
    print("\n  # ④ 同步快照并提交两个仓")
    print("  python tools\\market\\sync_snapshot.py --check")
    print(
        "\n**改配方后必须重跑本工具的第 2、3 步并重新签名** —— "
        f"清单覆盖 recipe.json 的字节（当前 {recipe_sha[:16]}…）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
