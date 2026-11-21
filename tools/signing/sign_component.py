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
import base64
import hashlib
import json
import os
import sys
import tempfile
import zipfile
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
from omnicrawler.plugins.signing import sign_bytes  # noqa: E402
from omnicrawler.services.component_manager import (  # noqa: E402
    _safe_component_path,
)

#: 组件包的载体文件名。它决定包类型，不能改。
MANIFEST_NAME = "component.json"
_REPO_ROOT = Path(__file__).resolve().parents[2]
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

    # **重签前必须先删旧的维护者签名**：清单变了，旧维护者签名对应的字节
    # 已经不是当前清单，留着会让签后自检报「存在维护者签名但未提供公钥」。
    # 顺序固定：创作者重签 → 维护者重签（后者由 maintainer-sign 负责）。
    stale_maintainer = directory / "package.manifest.maintainer.sig"
    stale_removed = False
    if stale_maintainer.is_file():
        stale_maintainer.unlink()
        stale_removed = True

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
    if stale_removed:
        print("  注意         = 旧的维护者签名已删除，需重跑 maintainer-sign")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """只验证，不签名。可在 CI 或发布前跑。"""
    directory = Path(args.component_dir).resolve()
    require_maintainer = bool(getattr(args, "require_maintainer", False))
    maintainer_key = getattr(args, "maintainer_key", None)
    public_key = None
    if maintainer_key:
        public_key = _public_of(Path(maintainer_key))
    elif require_maintainer:
        public_key = _public_of(
            _resolve_maintainer_key(None, "plugin_signing_private.pem")
        )
    try:
        verification = verify_package(
            directory,
            maintainer_public_key=public_key,
            require_maintainer=require_maintainer,
        )
    except PackageManifestError as exc:
        print(f"FAIL 验签失败：{exc}")
        return 1

    print(f"OK {directory.name}")
    print(f"  包类型       = {verification.package_type}")
    print(f"  包 id        = {verification.package_id}")
    print(f"  版本         = {verification.version}")
    print(f"  创作者指纹   = {verification.creator.key_fingerprint}")
    print(f"  清单覆盖     = {_manifest_file_count(directory)} 个文件")
    print(f"  维护者已签   = {verification.maintainer_signed}")
    return 0


#: 维护者私钥的**统一位置**（2026-11-21 维护者裁定）。
#:
#: 三把维护者私钥全部集中在这里，不再分散到别的目录：
#:   plugin_signing_private.pem   市场 catalog / 插件包
#:   update_signing_private.pem   应用更新清单
#:   component_signing_private.pem 组件（预留）
#:
#: **创作者身份不在这里** —— 它在 OS 密钥库（keyring），靠密码解锁。
#: 这是两套完全不同的东西，混淆会违反 §A.1.3「维护者不得冒充作者」。
MAINTAINER_KEY_DIR = Path(r"C:\Users\Lenovo\Desktop\档案")


def _resolve_maintainer_key(explicit: str | None, filename: str) -> Path:
    """定位维护者私钥：显式路径优先，否则用统一目录下的文件名。"""
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise SystemExit(f"维护者私钥不存在：{path}")
        return path
    path = MAINTAINER_KEY_DIR / filename
    if not path.is_file():
        raise SystemExit(
            f"维护者私钥不存在：{path}\n"
            "     用 --maintainer-key 指定其他路径"
        )
    return path


def _manifest_bytes(directory: Path) -> bytes:
    """待签的规范清单字节（与 verify 用的是同一份）。"""
    from omnicrawler.plugins.package_manifest import canonical_manifest_bytes

    manifest_path = directory / "package.manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(
            f"缺少 {manifest_path.name} —— 先跑 `sign` 做创作者签名"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    canonical = canonical_manifest_bytes(manifest)
    # 清单必须是规范字节，否则签出来的东西验不过
    if manifest_path.read_bytes() != canonical:
        raise SystemExit("清单不是规范 JSON，拒绝歧义签名字节")
    return canonical


def cmd_maintainer_sign(args: argparse.Namespace) -> int:
    """维护者对象级签名（`package.manifest.maintainer.sig`）。

    与创作者签名**分工明确**：

    | | 谁签 | 用什么 | 签什么 |
    |---|---|---|---|
    | 创作者签名 | 作者 | keyring 身份 | 「这是我写的」 |
    | 维护者签名 | 维护者 | 离线 PEM | 「我审过，可以进市场」 |

    `verify_package(require_maintainer=True)` 会强制要求这一轨。
    """
    from omnicrawler.plugins.package_manifest import MAINTAINER_SIGNATURE_NAME
    from omnicrawler.plugins.signing import sign_bytes

    directory = Path(args.component_dir).resolve()
    if not (directory / "package.manifest.json").is_file():
        raise SystemExit(
            f"{directory.name} 还没有创作者签名 —— "
            "先跑 `sign --username <创作者>` 再签维护者"
        )

    key_path = _resolve_maintainer_key(
        args.maintainer_key, args.key_filename or "plugin_signing_private.pem"
    )
    canonical = _manifest_bytes(directory)
    signature = sign_bytes(canonical, key_path.read_bytes())
    (directory / MAINTAINER_SIGNATURE_NAME).write_bytes(signature)

    # 自检：用刚写入的私钥对应公钥验一遍
    verification = verify_package(
        directory, maintainer_public_key=_public_of(key_path), require_maintainer=True
    )
    print(f"[维护者签名] 完成（{directory.name}）")
    print(f"  私钥         = {key_path.name}")
    print(f"  签名文件     = {MAINTAINER_SIGNATURE_NAME}")
    print(f"  维护者已签   = {verification.maintainer_signed}")
    print("  签名自检     = 通过")
    return 0


def _public_of(private_path: Path) -> bytes:
    """从私钥派生 Ed25519 公钥原始字节（只用于本地自检）。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit(f"{private_path} 不是 Ed25519 私钥")
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _repo_root() -> Path:
    """仓库根目录（可被环境变量覆盖，**仅供测试**）。

    为什么要这个开关：本仓的 pytest 临时目录配置在**仓库内部**，
    于是「私钥不得在仓内」这条判据无法用普通 tmp_path 测试 ——
    要么放宽判据（错），要么让判据的「仓界」可注入（对）。
    生产环境不设这个变量，行为完全不变。
    """
    override = os.environ.get("OMNICRAWL_SIGN_REPO_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return _REPO_ROOT


# ══════════════════════════════════════════════════════════════════════
# 以下为**原有 `.ocp` 包签名 API**，逐字保留。
#
# 为什么不能删：`tests/unit/tools/test_sign_component_tool.py` 6 条测试
# 依赖 `canonical_manifest` / `strip_signature` / `command_sign` /
# `command_inspect` 这组名字，而且 `canonical_manifest` 的守卫
# 是「工具规范化必须与客户端验签侧逐字相同」——那是签名正确性的根。
#
# 我一度把本文件整体重写成「签组件目录」，把这组 API 全丢了，
# 造成 6 条回归。教训：**扩展，不要替换**。
# ══════════════════════════════════════════════════════════════════════


def canonical_manifest(raw: dict) -> bytes:
    """规范化清单字节——**必须与客户端验签侧逐字一致**。

    客户端用的是：
        ``json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))``
    其中 ``raw`` 是**弹掉 signature 之后**的字典。任何差异（空格、排序、ensure_ascii）
    都会让签名验不过，所以这里刻意复用同一串参数，并加了守卫测试钉住一致性。
    """
    return json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def strip_signature(manifest: dict) -> dict:
    """返回弹掉 ``signature`` 的副本（不改原字典）。"""
    copied = dict(manifest)
    copied.pop("signature", None)
    return copied


def read_manifest(package: Path) -> tuple[dict, dict[str, bytes]]:
    """读出 ``component.json`` 与包内全部成员字节。"""
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        if MANIFEST_NAME not in names:
            raise SystemExit(f"组件包缺少 {MANIFEST_NAME}: {package}")
        manifest = json.loads(archive.read(MANIFEST_NAME))
        if not isinstance(manifest, dict):
            raise SystemExit(f"{MANIFEST_NAME} 必须是 JSON 对象")
        members = {name: archive.read(name) for name in names if name != MANIFEST_NAME}
    return manifest, members


def verify_local_hashes(manifest: dict, members: dict[str, bytes]) -> list[str]:
    """按清单里的 files 逐一核对包内成员哈希；返回问题列表。"""
    problems: list[str] = []
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        problems.append("清单 files 为空——签一个没有文件哈希的组件包没有意义")
        return problems
    for name, expected in files.items():
        try:
            safe = str(_safe_component_path(str(name)))
        except Exception as exc:  # noqa: BLE001
            problems.append(f"不安全的组件路径 {name!r}: {exc}")
            continue
        payload = members.get(safe)
        if payload is None:
            problems.append(f"清单声明了 {name} 但包里没有该文件")
            continue
        actual = hashlib.sha256(payload).hexdigest()
        if actual != expected:
            problems.append(f"{name} 哈希不匹配：清单={expected[:16]}… 实际={actual[:16]}…")
    return problems


def _write_package(target: Path, manifest: dict, members: dict[str, bytes]) -> None:
    """原子写回：先写临时名，再替换，避免中断留下半截包。"""
    target = target.resolve()
    with tempfile.NamedTemporaryFile(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", delete=False
    ) as handle:
        temp_path = Path(handle.name)
    try:
        with zipfile.ZipFile(temp_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))
            for name, payload in members.items():
                archive.writestr(name, payload)
        temp_path.replace(target)
    finally:
        temp_path.unlink(missing_ok=True)


def command_inspect(args: argparse.Namespace) -> int:
    package = Path(args.package).expanduser()
    manifest, members = read_manifest(package)
    body = strip_signature(manifest)
    problems = verify_local_hashes(body, members)
    print(json.dumps({
        "package": str(package),
        "name": body.get("name"),
        "version": body.get("version"),
        "files": len(body.get("files") or {}),
        "already_signed": "signature" in manifest,
        "canonical_sha256": hashlib.sha256(canonical_manifest(body)).hexdigest(),
        "self_check": problems or "ok",
    }, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


def command_sign(args: argparse.Namespace) -> int:
    package = Path(args.package).expanduser()
    if not package.is_file():
        raise SystemExit(f"组件包不存在: {package}")
    # 冷密钥安全：绝不从仓库内路径读私钥
    private_path = Path(args.private_key).expanduser()
    if not private_path.is_file():
        raise SystemExit(f"私钥不存在: {private_path}")
    try:
        private_path.resolve().relative_to(_repo_root())
    except ValueError:
        pass  # 不在仓库内 —— 正常
    else:
        raise SystemExit(
            f"拒绝使用仓库内的私钥文件: {private_path}\n"
            "冷私钥必须放在仓外（例如桌面档案目录或加密盘）。"
        )

    manifest, members = read_manifest(package)
    body = strip_signature(manifest)
    problems = verify_local_hashes(body, members)
    if problems:
        raise SystemExit(
            "签名前自检失败，已拒绝签名（签一个哈希对不上的包没有意义）：\n  "
            + "\n  ".join(problems)
        )

    signature = sign_bytes(canonical_manifest(body), private_path.read_bytes())
    signed = dict(body, signature=base64.b64encode(signature).decode())
    _write_package(package, signed, members)
    print(json.dumps({
        "package": str(package),
        "signed": True,
        "name": body.get("name"),
        "version": body.get("version"),
        "files": len(body.get("files") or {}),
    }, ensure_ascii=False, indent=2))
    return 0



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="组件签名工具（创作者身份；与 sign_plugin.py 同等地位）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── 原有 `.ocp` 包签名（原版 CLI，保留）────────────────────────────
    ps = sub.add_parser("package-sign", help="给 .ocp 组件包签名（原地更新）")
    ps.add_argument("--package", required=True, help="组件包路径")
    ps.add_argument("--private-key", required=True, help="签名私钥 PEM 路径（仓外）")
    ps.set_defaults(func=command_sign)

    ins = sub.add_parser("inspect", help="只读：自检 .ocp 包哈希并打印规范化摘要")
    ins.add_argument("--package", required=True, help="组件包路径")
    ins.set_defaults(func=command_inspect)

    # ── 组件目录签名（本仓新增，与上面签的是不同对象）─────────────────
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

    ms = sub.add_parser(
        "maintainer-sign",
        help="维护者对象级签名（package.manifest.maintainer.sig）",
    )
    ms.add_argument("component_dir", help="已由创作者签名过的组件目录")
    ms.add_argument(
        "--maintainer-key", default=None,
        help=f"维护者私钥路径（缺省用 {MAINTAINER_KEY_DIR}\\plugin_signing_private.pem）",
    )
    ms.add_argument(
        "--key-filename", default=None,
        help="统一目录下的私钥文件名（默认 plugin_signing_private.pem）",
    )
    ms.set_defaults(func=cmd_maintainer_sign)

    vf = sub.add_parser("verify", help="验证组件清单与创作者签名")
    vf.add_argument("component_dir", help="组件目录")
    vf.add_argument(
        "--require-maintainer", action="store_true",
        help="同时要求并验证维护者签名（市场发布包必须）",
    )
    vf.add_argument(
        "--maintainer-key", default=None,
        help="维护者私钥路径（用于派生公钥验维护者轨）",
    )
    vf.set_defaults(func=cmd_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
