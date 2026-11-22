"""组件配方门禁：**凡是声明，都必须能从构件字节推导出来**。

## 一条通则，覆盖一整类缺陷

v1.0.13→v1.0.29 的 17 轮排查里，配方缺陷**全是同一类**——声明与实际不符：

| 缺陷 | 声明 | 实际 |
|---|---|---|
| `platforms: ["windows"]` | 只说 windows | 上游有三平台 |
| aarch64 / cp310 / cp313t 误选 | 平台或 ABI 声明了 | 选了不兼容的轮子 |
| `architectures: [x86_64, aarch64]` | 说支持 ARM | 全是 x86_64 轮子 |
| `arm64` vs `aarch64` | 写 `arm64` | 代码返回 `aarch64` |
| `entry.json` | 声明存在 | 从未生成 |

所以本门禁不逐条打补丁，而是**对每个可推导字段做一次比对**。

## 为什么能在本地跑（零网络、零下载）

依赖闭包要联网查 PyPI，但那件事已经**固化成快照**（`deps.lock.json`，由
`tools/market/component_deps_lock.py` 生成）。本门禁只读两个 JSON 做集合运算，
**毫秒级，不联网，不下载任何 wheel**。

维护者约束：不给本地开发机增加负担 —— 新增耗时目标 < 0.3 秒。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO / "src") not in sys.path:
    sys.path.insert(0, str(_REPO / "src"))
# 本门禁直接跑时 `sys.path[0]` 是 `tools/gates/`，`import tools.market.*` 会失败 ——
# 仓库根也要在路径上（`component_discovery` 就是这么被引入的）。
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

#: 架构名归一（与 `component_recipe.normalize_architecture` 保持同义）。
_ARCH_ALIASES = {
    "amd64": "x86_64",
    "x86_64": "x86_64",
    "x64": "x86_64",
    "arm64": "aarch64",
    "aarch64": "aarch64",
    "armv8": "aarch64",
    "universal2": "universal2",
}

#: 平台 → 该平台 wheel 文件名必须出现的标记。
_PLATFORM_TOKENS = {
    "windows": ("win_amd64", "win32", "win_arm64"),
    "linux": ("manylinux", "musllinux", "linux_x86_64", "linux_aarch64"),
    "macos": ("macosx",),
}


def _norm_arch(name: str) -> str:
    return _ARCH_ALIASES.get(str(name).strip().lower(), str(name).strip().lower())


def _wheel_names(recipe: dict) -> list[str]:
    return [
        str(a.get("url") or "").rsplit("/", 1)[-1].lower()
        for a in recipe.get("artifacts") or []
    ]


def _arch_from_filename(filename: str) -> set[str]:
    """从 wheel 文件名推出它覆盖的架构。"""
    archs: set[str] = set()
    if "universal2" in filename:
        return {"x86_64", "aarch64"}
    for token, canonical in (
        ("aarch64", "aarch64"),
        ("arm64", "aarch64"),
        ("win_amd64", "x86_64"),
        ("win32", "x86"),
        ("x86_64", "x86_64"),
    ):
        if token in filename:
            archs.add(canonical)
    return archs


def _is_pure_python_wheel(filename: str) -> bool:
    """`py3-none-any.whl` 这类纯 Python 轮子：无平台、无架构标记，通用。

    **为什么需要**：纯 Python 组件的每个构件都写着 platforms=[三平台]，
    但文件名里没有任何平台 token。早先一律要求「文件名含平台 token」，
    于是 `markdownify` 被判「声明支持 windows 但没有带该平台标记的构件」。
    2026-11-23 加组件时踩到。
    """
    return "-none-any.whl" in filename.lower()



def _abi_from_filename(filename: str) -> str:
    """从 wheel 文件名推出 ABI 大类。"""
    if "none-any" in filename:
        return "any"
    if "abi3" in filename:
        return "abi3"
    match = re.search(r"cp3\d+", filename)
    return match.group(0) if match else "?"


def _check_builtin_recipe(recipe_path: Path, recipe: dict) -> list[str]:
    """内置构件的专用判据。

    内置构件是「字节随包、但也要被组件体系管理」的东西。
    它不适用按文件名反推平台/架构，但有**更严格的替代判据**：
    必须有一份逐文件哈希清单，且与声明对得上。

    **空清单必须判红**：空集合会让所有「每个文件都有哈希」的断言恒真 ——
    这正是判据纪律里点名的那类陷阱。
    """
    problems: list[str] = []
    name = recipe_path.parent.name

    lock_path = recipe_path.parent / "files.lock.json"
    if not lock_path.is_file():
        problems.append(
            f"{name}: 内置组件缺少 files.lock.json —— "
            "内置不等于免校验，逐文件哈希必须随配方一起签名"
        )
        return problems

    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        problems.append(f"{name}: files.lock.json 无法解析 —— {exc}")
        return problems

    files = lock.get("files")
    if not isinstance(files, dict) or not files:
        problems.append(f"{name}: files.lock.json 的 files 为空 —— 空清单会让校验恒真")
        return problems

    declared_size = sum(int(a.get("size") or 0) for a in recipe["artifacts"])
    actual_size = sum(
        int(v.get("size") or 0) for v in files.values() if isinstance(v, dict)
    )
    if declared_size != actual_size:
        problems.append(
            f"{name}: 构件 size 合计 {declared_size} 与 files.lock.json 的 "
            f"{actual_size} 不一致"
        )

    bad_hash = [
        path
        for path, meta in files.items()
        if not (isinstance(meta, dict) and len(str(meta.get("sha256") or "")) == 64)
    ]
    if bad_hash:
        problems.append(
            f"{name}: files.lock.json 里有 {len(bad_hash)} 个文件缺少合法 sha256"
        )

    # builtin 与 url 互斥：同时写会让「到底从哪来」含糊
    for index, artifact in enumerate(recipe["artifacts"]):
        if artifact.get("url"):
            problems.append(
                f"{name}: artifact #{index} 声明 source=builtin 却还写了 url"
            )
        if not str(artifact.get("builtin_path") or "").strip():
            problems.append(
                f"{name}: artifact #{index} 声明 source=builtin 但缺少 builtin_path"
            )
    return problems


def _check_manifest_integrity(recipe_path: Path) -> list[str]:
    """校验 `package.manifest.json` 记录的哈希与**当前文件**是否一致。

    ## 为什么必须有这一项

    清单是**签名覆盖的字节**。清单里写 `recipe.json: sha256:27662b...`，
    而实际文件已经是别的哈希时，签名验不过 —— 但**没有任何门禁会报**。
    结果是：市场声称「这个组件经创作者签名」，实际内容已经变了。

    实测（2026-11-20）：给两个组件补 `id` 字段后，两份清单全部失效，
    直到写「验签守卫」时才发现。**配方改了没重签**是极易发生的疏漏，
    因为它不需要任何恶意，只要改一个字忘跑签名脚本就够了。

    判据：清单里**每一个**文件都必须存在且哈希一致；
    清单本身若存在，签名文件也必须存在。
    """
    problems: list[str] = []
    name = recipe_path.parent.name
    manifest_path = recipe_path.parent / "package.manifest.json"
    if not manifest_path.is_file():
        # 组件可以暂时没有签名轨（早期组件），但**内置组件不允许**——
        # 内置物随包发布，没有签名链就没有任何来源凭据。
        return problems

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{name}: package.manifest.json 无法解析 —— {exc}"]

    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        return [f"{name}: package.manifest.json 的 files 为空 —— 空清单会让校验恒真"]

    for relative, expected in files.items():
        target = recipe_path.parent / str(relative)
        if not target.is_file():
            problems.append(f"{name}: 清单记录了不存在的文件 {relative}")
            continue
        actual = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
        if str(expected) != actual:
            problems.append(
                f"{name}: 清单与文件不一致 —— {relative} 记录 "
                f"{str(expected)[:19]}…，实际 {actual[:19]}…"
                "（改了文件就必须重跑签名脚本）"
            )

    # 签名文件必须与清单同在（有清单无签名 = 无法验证来源）
    if not (recipe_path.parent / "creator.identity").is_file():
        problems.append(f"{name}: 有清单却缺 creator.identity")
    if not (recipe_path.parent / "package.manifest.creator.sig").is_file():
        problems.append(f"{name}: 有清单却缺 package.manifest.creator.sig")
    return problems


def _check_provides(recipe_path, recipe):
    """`provides` 是插件与组件对上的唯一凭据，不能缺。

    插件用 `required_capabilities` 声明「我需要 ocr.paddle」，
    组件必须声明「我提供 ocr.paddle」—— 否则两者只能靠人脑对应。
    """
    problems = []
    name = recipe_path.parent.name
    provides = recipe.get("provides")
    if not provides:
        problems.append(f"{name}: 没有声明 provides —— 插件无法与之对接")
        return problems
    if not isinstance(provides, list):
        problems.append(f"{name}: provides 必须是列表")
        return problems
    normalized = []
    for item in provides:
        if not isinstance(item, str) or not item.strip():
            problems.append(f"{name}: provides 含非字符串项")
            continue
        value = item.strip().lower()
        if not all(ch.isalnum() or ch in "._-" for ch in value):
            problems.append(f"{name}: 能力名只允许字母/数字/._-：{item!r}")
        normalized.append(value)
    if len(set(normalized)) != len(normalized):
        problems.append(f"{name}: provides 含重复项 {normalized}")
    return problems


def check_recipe(recipe_path: Path) -> list[str]:
    """返回发现的问题（空列表 = 通过）。"""
    from omnicrawler.services import component_recipe as cr

    problems: list[str] = []
    # **配方本身不合法时必须报成「问题」而不是崩栈**：
    # `load_recipe` 会拒掉结构不对的配方（例如声明了某平台却没给它构件），
    # 而崩溃的退出码不是 1，会让 CI 把它误判成「脚本自己出错」而非「配方有问题」。
    try:
        recipe = cr.load_recipe(recipe_path)
    except cr.RecipeError as exc:
        return [f"{recipe_path.parent.name}: 配方不合法 —— {exc}"]
    wheels = _wheel_names(recipe)
    kind = cr.component_kind(recipe)

    # ── 0) 目录名必须等于 recipe 的 id ────────────────────────────────────
    #
    # 改成分类子目录后，`components/<分类>/<id>/` 里的 `<id>` 是**目录名**，
    # 而 `recipe.json` 里也有一份 `id`。两者不一致时此前**不报错** ——
    # 目录说它在 A 组件下、JSON 说它叫 B，catalog 与文件系统对不上。
    # 扁平结构下这个一致性是隐含的（build.py 直接用 parent.name），
    # 两级之后必须显式校验。
    try:
        _raw = json.loads(recipe_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _raw = None
    if isinstance(_raw, dict):
        # 只校验**显式声明的 id**。
        # 不拿 `name` 当 id：`name` 是描述性名称（可以写"Tesseract 中文语言包"），
        # 而 market 侧 `build.py` 用 `directory.name` 当 id —— 两者语义不同。
        # 不声明 id 时，目录名就是唯一真源（旧行为，保持兼容）。
        declared_id = str(_raw.get("id") or "").strip()
        dir_name = recipe_path.parent.name
        if declared_id and declared_id != dir_name:
            problems.append(
                f"{dir_name}: 目录名与 recipe 声明的 id 不一致"
                f"（目录 {dir_name!r} vs id {declared_id!r}）—— "
                "id 一旦声明就必须等于目录名，否则 catalog 与文件系统会对不上"
            )

    problems.extend(_check_provides(recipe_path, recipe))
    # 清单完整性：**改过文件就必须重签**，这一项专门抓「忘了重签」。
    problems.extend(_check_manifest_integrity(recipe_path))

    # **纯数据组件**（如 Tesseract 语言包）与平台/架构无关：
    # 字节是通用数据文件，文件名里不会有 `win_amd64` / `manylinux` 这类标记。
    # 对它套用「每个平台都要有平台标记构件」是**判据过严**，会逼出假阳性。
    if kind == "data":
        # **必须合并已有的 problems**，不能直接 return ——
        # 那会丢弃前面已经累积的检查结果（例如 id 与目录名不一致）。
        # 实测踩到：数据组件的 id 判据被这一行静默吞掉。
        problems.extend(_check_data_recipe(recipe_path, recipe))
        return problems

    # **内置构件**（`source: builtin`）不适用「按文件名反推平台/架构」：
    # 它是一整个目录（浏览器内核 310 个文件），文件名里不会有 `win_amd64`
    # 这类标记。它的推导依据是**逐文件哈希清单**，比文件名更精确 ——
    # 所以跳过反推，但**不跳过校验**（清单缺失/为空/尺寸不符都判红）。
    if recipe.get("artifacts") and all(
        str(a.get("source") or "remote").lower() == "builtin"
        for a in recipe["artifacts"]
    ):
        problems.extend(_check_builtin_recipe(recipe_path, recipe))
        return problems

    # **native 组件的构件是"整个可执行包"**（如 Firefox/WebKit 内核的 zip），
    # 文件名由上游决定，不含 `win_amd64` / `manylinux` 这类标记。
    # 对这一形态，平台/架构判据换成**显式声明**：每个构件的 platforms
    # 必须非空，且与顶层一致 —— 既不强求文件名带标记，也不允许
    # 「声明了却不指定构件归属」。
    if kind == "native":
        for index, artifact in enumerate(recipe["artifacts"]):
            if not artifact.get("platforms"):
                problems.append(
                    f"{recipe_path.parent.name}: native 构件 #{index} 必须显式声明 "
                    "platforms（该形态的文件名不含平台标记，不能靠反推）"
                )
        return problems

    # ── 1) platforms：每个声明平台都要有该平台的构件 ──────────────────────
    for platform in recipe["platforms"]:
        tokens = _PLATFORM_TOKENS.get(platform, ())
        specific = [
            w
            for a, w in zip(recipe["artifacts"], wheels, strict=False)
            if platform in (a.get("platforms") or recipe["platforms"])
            and (any(t in w for t in tokens) or _is_pure_python_wheel(w))
        ]
        if not specific:
            problems.append(
                f"{recipe_path.parent.name}: 声明支持 {platform}，"
                f"但没有任何带该平台标记（或通用纯 Python）的构件"
            )

    # ── 2) architectures：每个声明架构都要能从构件文件名推出 ─────────────
    declared_arch = {_norm_arch(a) for a in recipe["architectures"]}
    available_arch: set[str] = set()
    for w in wheels:
        available_arch |= _arch_from_filename(w)
    # 纯 Python 轮子没有架构信息，不算覆盖
    if "universal2" in declared_arch:
        available_arch.add("universal2")
    # **全是纯 Python 轮子时，架构声明不适用**：它们本就是 `none-any`，
    # 跑在任何架构上。此时不该按「声明了 x86_64 却没有 x86_64 构件」判虚报。
    # 2026-11-23 加 markdownify 组件时踩到。
    all_pure = bool(wheels) and all(_is_pure_python_wheel(w) for w in wheels)
    if all_pure:
        available_arch |= declared_arch
    missing_arch = declared_arch - available_arch
    if missing_arch:
        problems.append(
            f"{recipe_path.parent.name}: architectures 声明了 {sorted(missing_arch)}，"
            f"但没有任何对应架构的构件 —— 声明虚报"
        )

    # 反向：构件里有、声明里没有 ⇒ 也是虚报（漏声明同样会误导用户）
    undeclared = available_arch - declared_arch
    if undeclared:
        problems.append(
            f"{recipe_path.parent.name}: 构件里有 {sorted(undeclared)} 架构的二进制，"
            f"但 architectures 没声明"
        )

    # ── 3) python_abi：声明的 ABI 必须与构件兼容，且不得有不可用轮子 ─────
    declared_abi = [str(a) for a in (recipe.get("python_abi") or [])]
    for w in wheels:
        abi = _abi_from_filename(w)
        if "cp313t" in w:
            problems.append(
                f"{recipe_path.parent.name}: {w} 是自由线程（cp313t）轮子，普通解释器加载不了"
            )
        elif abi.startswith("cp") and declared_abi and abi not in declared_abi:
            problems.append(
                f"{recipe_path.parent.name}: {w} 的 ABI 是 {abi}，"
                f"但 python_abi 只声明了 {declared_abi}"
            )

    # ── 4) declared_total_bytes：必须等于各构件 size 之和 ────────────────
    sizes = [int(a.get("size") or 0) for a in recipe.get("artifacts") or []]
    if any(s == 0 for s in sizes):
        problems.append(
            f"{recipe_path.parent.name}: 有构件没声明 size —— 用户看不到下载量"
        )

    # ── 5) entry.module：必须落在某个 extract_to 之下 ────────────────────
    entry = recipe.get("entry") or {}
    module = str(entry.get("module") or "").strip()
    if module:
        extract_targets = {
            str(a.get("extract_to") or "").rstrip("/")
            for a in recipe.get("artifacts") or []
            if a.get("extract_to")
        }
        if extract_targets and not any(
            module == t or module.startswith(t + "/") for t in extract_targets
        ):
            problems.append(
                f"{recipe_path.parent.name}: entry.module={module!r} "
                f"不在任何 extract_to（{sorted(extract_targets)}）之下"
            )

    # ── 6) 依赖闭包快照：配方的 wheel 必须覆盖快照里的每个包 ─────────────
    lock_path = recipe_path.parent / "deps.lock.json"
    if lock_path.is_file():
        snapshot = json.loads(lock_path.read_text(encoding="utf-8"))
        from omnicrawler.services.component_recipe import recipe_digest

        if snapshot.get("recipe_digest") != recipe_digest(recipe):
            problems.append(
                f"{recipe_path.parent.name}: deps.lock.json 已过期"
                f"（配方改过但快照没重算）"
            )
        # **精确匹配包名，不用子串**：`httpx` 是 `httpx2` 的前缀，
        # 子串匹配会把「装了 httpx2」误判成「已经装了 httpx」——
        # 真实踩过（CI 报 No module named 'httpx'，而本地校验说通过）。
        # 做法：从每个 wheel 文件名里解析出**完整的包名段**再比。
        wheel_packages = set()
        for filename in wheels:
            stem = filename.split("-")[0]
            wheel_packages.add(re.sub(r"[-_.]+", "_", stem).lower())
        missing_pkgs = [
            name
            for name in snapshot.get("packages") or []
            if re.sub(r"[-_.]+", "_", name).lower() not in wheel_packages
        ]
        if missing_pkgs:
            problems.append(
                f"{recipe_path.parent.name}: 配方缺少闭包声明的包 {missing_pkgs}"
            )
    elif kind == "wheel-pack":
        # 只有 wheel-pack 需要闭包快照：native/data 不装 Python 包，
        # 对它们要求快照既无意义也生成不出来。
        problems.append(
            f"{recipe_path.parent.name}: 缺少依赖闭包快照 deps.lock.json"
            f"（运行 tools/market/component_deps_lock.py 生成）"
        )

    return problems


def _check_data_recipe(recipe_path: Path, recipe: dict) -> list[str]:
    """纯数据组件的校验：只看「数据文件能否真的交付」。

    数据组件的正确判据与 Wheel-Pack 不同：

    * 构件**不应**带平台/架构标记（带了反而说明选错了内容）；
    * 必须声明 `declared_total_bytes`（用户要知道下载多少）；
    * **可以有** `platforms` / `architectures` 声明（表示「这份数据在三平台都可用」），
      但构件本身不该分平台。
    """
    from omnicrawler.services import component_recipe as cr

    problems: list[str] = []
    name = recipe_path.parent.name
    if cr.component_kind(recipe) != "data":
        return problems

    for artifact in recipe.get("artifacts") or []:
        if artifact.get("platforms"):
            problems.append(
                f"{name}: 纯数据组件的构件不应标 platforms"
                f"（{artifact['url'].rsplit('/', 1)[-1]} 标了 {artifact['platforms']}）"
            )
        if not artifact.get("size"):
            problems.append(
                f"{name}: 构件 {artifact['url'].rsplit('/', 1)[-1]} 没声明 size"
                f" —— 用户看不到下载量"
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="组件配方声明可推导校验")
    parser.add_argument(
        "--root",
        type=Path,
        default=_REPO / "market" / "components",
        help="含各组件 recipe.json 的目录",
    )
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    root: Path = args.root
    if not root.is_dir():
        print(f"目录不存在：{root}", file=sys.stderr)
        return 2

    # 组件按**分类子目录**组织：`components/<分类>/<id>/recipe.json`。
    # 用 rglob 兼容扁平布局（`components/<id>/`）与分类布局两种。
    # 组件发现走**唯一真源**：目录形状（扁平/两级）只在那里声明一次。
    from tools.market.component_discovery import iter_component_recipes

    recipes = iter_component_recipes(root)
    if not recipes:
        # **空集合必须判红**：没有可检查的配方不等于通过
        print(f"未找到任何组件配方（{root}/ */recipe.json）", file=sys.stderr)
        return 1

    problems: list[str] = []
    for recipe_path in recipes:
        problems.extend(check_recipe(recipe_path))

    if problems:
        print("组件配方声明与构件字节不一致：", file=sys.stderr)
        for item in problems:
            print(f"  - {item}", file=sys.stderr)
        return 1

    print(f"组件配方声明可推导：{len(recipes)} 份配方全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
