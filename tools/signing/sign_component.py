"""组件签名工具 —— 与 `sign_plugin.py` 平级的**组件专用**入口。

## 为什么需要这个文件

`sign_plugin.py` 的 `creator-sign` **已经能处理组件**（载体是 `recipe.json`
时包类型判为 `component`），但它叫"plugin"、参数叫 `--file plugin.py`、
帮助文本里组件只是一句附注。结果是：

* 维护者不知道组件该用哪个工具，于是照着"插件工具"的用法猜；
* 出现「用维护者 PEM 私钥给组件签创作者名」这种错用
  （违反 §A.1.3「维护者不得冒充作者」）；
* 组件与插件在**工具层面看起来不是同等地位**，而它们**应该**是。

本文件把组件这条路显式化：**同一个创作者身份、同一套保险库、同一条签名链**，
只是入口和预设不同。

## 身份模型（必须先理解，否则会签错）

```text
创作者身份 = Ed25519 密钥对，密码加密后存进 OS 密钥库（keyring）
             ↑ 这是「作者」身份，插件/模板/组件共用同一个
维护者私钥 = PEM 文件，离线保管，只用于签 catalog
             ↑ 永不进仓库；**不得**用来给组件签创作者名
```

组件、插件、模板三者**同一创作者身份**（本机实测只有一个 `starlife`）。
签名产出 `creator.identity`（含公钥与指纹）+ `package.manifest.json` + 双签名轨。

## 与 `sign_plugin.py` 的关系

不重新实现任何签名逻辑 —— 直接复用
`omnicrawler.plugins.package_manifest.sign_creator_package`。
本文件只做三件事：

1. **预设组件参数**（载体固定 `recipe.json`、包类型固定 `component`）；
2. **组件专属的前置校验**：`recipe.json` 必须已通过 `load_recipe`
   （否则会给一份非法配方签名，签完才在装配时报错）；
3. **组件专属的后置自检**：签完立刻用 `verify_package` 验一遍，
   确认公钥能验过刚写入的签名。

## 用法

```powershell
# 身份密码走环境变量（推荐），不要写在命令行里
$sec = Read-Host -AsSecureString "创作者身份密码"
$env:OMNICRAWL_IDENTITY_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))

python tools/signing/sign_component.py sign <组件目录> --username starlife
python tools/signing/sign_component.py verify <组件目录>
```
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ── 自引导：让「直接 python tools/signing/sign_component.py」也能找到包 ──
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from omnicrawler.plugins.identity import CreatorIdentity, IdentityStore  # noqa: E402
from omnicrawler.plugins.package_manifest import (  # noqa: E402
    PackageManifestError,
    sign_creator_package,
    verify_package,
)

#: 组件包的载体文件名。它决定包类型，不能改。
RECIPE_FILENAME = "recipe.json"

#: 组件目录里**不参与清单**的文件（签名自身与派生数据）。
#: 与 `package_manifest._payload_files` 的口径保持一致：
#: 清单只覆盖"被声明的字节"，签名产物本身不能进清单（否则自引用）。
_NON_PAYLOAD = frozenset(
    {
        "package.manifest.json",
        "creator.sig",
        "package.manifest.creator.sig",
        "deps.lock.json",  # 派生快照：由 component_deps_lock.py 生成，非手写内容
        "files.lock.json",  # 内置组件的逐文件清单：体量大且由工具生成
    }
)


def _manifest_file_count(directory: Path) -> int:
    """清单覆盖的文件数。

    `PackageVerification` 只携带摘要与身份，不带 manifest 本体 ——
    所以这里直接读 `package.manifest.json`。
    """
    manifest_path = directory / "package.manifest.json"
    if not manifest_path.is_file():
        return 0
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    files = manifest.get("files")
    return len(files) if isinstance(files, dict) else 0


def _resolve_password(password: str | None) -> str:
    """密码来源：`--password` > 环境变量（与 identity.py 同一条纪律）。"""
    import os

    if password:
        return password
    value = os.environ.get("OMNICRAWL_IDENTITY_PASSWORD", "")
    if not value:
        raise SystemExit(
            "需要身份密码：设 OMNICRAWL_IDENTITY_PASSWORD 环境变量，或用 --password"
            "（推荐环境变量：命令行参数会进 shell 历史）"
        )
    return value


def _require_valid_recipe(component_dir: Path) -> dict:
    """签名前先校验配方。

    **为什么签之前要校验**：给一份非法配方签名，等于用创作者身份背书一个
    装不上的东西。等到装配时才报错，签名已经发出去了。

    这里只做结构校验（`load_recipe`），不联网、不下载。
    """
    from omnicrawler.services.component_recipe import RecipeError, load_recipe

    recipe_path = component_dir / RECIPE_FILENAME
    if not recipe_path.is_file():
        raise SystemExit(f"不是组件目录（缺 {RECIPE_FILENAME}）：{component_dir}")
    try:
        return load_recipe(recipe_path)
    except RecipeError as exc:
        raise SystemExit(f"配方校验失败，拒绝签名：{exc}") from exc


def _component_version(recipe: dict, directory: Path) -> str:
    """版本取自配方本身 —— 它决定安装目录名，不能靠目录推断。"""
    version = str(recipe.get("version") or "").strip()
    if not version:
        raise SystemExit(f"配方缺少 version：{directory / RECIPE_FILENAME}")
    return version


def _component_id(recipe: dict, directory: Path) -> str:
    """包 id：优先配方显式声明的 `id`，否则用目录名。

    与 catalog 侧同口径（`build.py` 用 `parent.name`），
    且门禁强制「声明了 id 就必须等于目录名」。
    """
    declared = str(recipe.get("id") or "").strip()
    if declared and declared != directory.name:
        raise SystemExit(
            f"配方 id（{declared!r}）与目录名（{directory.name!r}）不一致 —— "
            "门禁会拒，签名前必须先对齐"
        )
    return declared or directory.name


def cmd_sign(args: argparse.Namespace) -> int:
    directory = Path(args.component_dir).resolve()
    recipe = _require_valid_recipe(directory)
    password = _resolve_password(args.password)

    try:
        identity = IdentityStore().load(args.username, password)
    except Exception as exc:  # noqa: BLE001 - 身份库错误要如实报
        print(f"FAIL 加载创作者身份 {args.username!r}: {exc}")
        return 1

    package_id = _component_id(recipe, directory)
    version = _component_version(recipe, directory)

    # 已签名且不是重签时拒绝 —— 防误改已发布的创作者身份。
    identity_path = directory / "creator.identity"
    if identity_path.is_file() and not args.force:
        existing = CreatorIdentity.from_dict(
            json.loads(identity_path.read_text(encoding="utf-8"))
        )
        if existing.key_fingerprint != identity.key_fingerprint:
            print(
                f"FAIL {directory.name} 已有创作者身份 "
                f"{existing.key_fingerprint}，与当前身份 "
                f"{identity.key_fingerprint} 不同。\n"
                "     替换已发布组件的创作者身份会改变所有权语义，必须显式 --force，"
                "并在文档里记录原因。"
            )
            return 1
        print(
            f"NOTE {directory.name} 已由同一身份签名过；"
            "将按当前字节重签（内容变了就必须重签，否则清单失效）"
        )

    signed = sign_creator_package(
        directory,
        package_type="component",
        package_id=package_id,
        version=version,
        identity=identity,
        legacy_target=RECIPE_FILENAME,
    )

    # ── 后置自检：刚写进去的签名必须能验过 ────────────────────────────
    try:
        verify_package(directory)
    except PackageManifestError as exc:
        print(f"FAIL 签名后自检失败：{exc}")
        return 1

    # `PackageVerification` 不携带 manifest —— 清单覆盖数要直接读文件。
    # （我第一版写成 verification.manifest，导致签名成功后崩在最后一行打印，
    #  用户看到 traceback 容易误判为签名失败。）
    covered = _manifest_file_count(directory)

    print(
        f"[组件签名] 完成（作者: {args.username}，指纹: "
        f"{signed.creator.key_fingerprint}，包哈希: {signed.manifest_sha256[:16]}…）"
    )
    print(f"  组件 id      = {package_id}")
    print(f"  版本         = {version}")
    print(f"  清单覆盖     = {covered} 个文件")
    print("  签名自检     = 通过")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """只验证，不签名。可在 CI 或发布前跑。"""
    directory = Path(args.component_dir).resolve()
    try:
        verification = verify_package(directory)
    except PackageManifestError as exc:
        print(f"FAIL 验签失败：{exc}")
        return 1

    print(f"OK {directory.name}")
    print(f"  包类型       = {verification.package_type}")
    print(f"  包 id        = {verification.package_id}")
    print(f"  版本         = {verification.version}")
    print(f"  创作者指纹   = {verification.creator.key_fingerprint}")
    print(f"  清单覆盖     = {_manifest_file_count(directory)} 个文件")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="组件签名工具（创作者身份；与 sign_plugin.py 同等地位）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sg = sub.add_parser("sign", help="用创作者身份签名组件（生成清单与双签名）")
    sg.add_argument("component_dir", help="组件目录（须含 recipe.json）")
    sg.add_argument("--username", required=True, help="本地创作者身份用户名")
    sg.add_argument(
        "--password", default=None,
        help="身份密码（默认读 OMNICRAWL_IDENTITY_PASSWORD；建议用环境变量）",
    )
    sg.add_argument(
        "--force", action="store_true",
        help="允许替换已存在的**不同**创作者身份（默认拒绝，防误改已发布所有权）",
    )
    sg.set_defaults(func=cmd_sign)

    vf = sub.add_parser("verify", help="验证组件清单与创作者签名")
    vf.add_argument("component_dir", help="组件目录")
    vf.set_defaults(func=cmd_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
