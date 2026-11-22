"""组件装配 recipe：**声明式**描述"从哪拉、怎么拼"，拒绝可执行。

定位（条目 5 形态 B）
--------------------
用户决定的分工是：**下载从上游拉，需要组装就在本地组装，本项目只维护组装方式。**

所以这里只有"怎么做"的声明，没有"做什么"的代码：
* 不托管字节（没有我们的 CDN，下载走既有的受控出站）；
* **recipe 不是可执行脚本**——给脚本执行权限等于在信任边界上开任意执行口。

为什么 recipe 本身也必须可校验
------------------------------
recipe 决定"从哪个 URL 拉、解压到哪里、入口是什么"。它能被改，
就等于能改组件的实际来源。因此：

* 每个构件**必须**带 sha256（缺一条就拒绝整份，不做"部分信任"）；
* recipe 内容可算稳定摘要（`:func:`recipe_digest```），便于纳入签名清单；
* 平台/架构/ABI 不匹配时**在装配前**拒绝，避免装到一半才失败。

装配是**两阶段**的
------------------
先全部校验（哈希、路径安全、兼容性），再落盘。任何一步失败都不留半成品——
半装状态比"没装"更难排查。

安全边界
--------
解压复用 `core.archive_security` 的校验（zip-slip / 路径穿越 / 大小限制），
不自写一套解压——那正是最容易漏掉路径规范化的地方。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tarfile
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from ..core.archive_security import (
    DEFAULT_ZIP_READ_LIMITS,
    UnsafePackageError,
    copy_zip_member,
)

#: 支持的 recipe schema 版本。未知版本一律拒绝（不猜测、不降级）。
#: 支持的 recipe schema 版本。
#:
#: v1 → v2 的差别（§15.8）：
#:
#: * **新增 `requires`**：配方能**表达依赖图**。此前依赖只有 `deps.lock.json`
#:   这份**旁路快照**，配方本身看不出「我需要什么」——而 pip 存在的理由
#:   就是「手写依赖必然漏」（实测漏过 httpx / httpcore / networkx /
#:   protobuf / safetensors 等）。
#: * **新增 `platform_matrix`**：每平台显式枚举 `{arch, abi, url|builtin_path,
#:   sha256}`，取代 v1 的过渡字段 `platform_architectures`。
#:   过渡字段表达不了「同一平台不同架构用不同 ABI/构件」，
#:   而 `paddlepaddle` 的 macOS 轮子只有 arm64 就是这类情况。
#:
#: **两版都接受**：迁移期 v1 配方继续可用，但 `platform_architectures`
#: 与 `platform_matrix` **不得同时出现**（不保留双写尾巴）。
SUPPORTED_SCHEMA = (1, 2)

#: recipe 文件只接受这些扩展名。**刻意不含** .sh/.bat/.py/.exe/.ps1：
#: recipe 必须是数据，不是程序。
ALLOWED_RECIPE_SUFFIXES = (".json",)

#: 支持的构件归档/落地格式：
#: - zip / whl：PEP 427 wheel 本质即纯数据 ZIP 归档（无安装脚本）；
#: - tar.gz：常见 Linux/macOS 原生工具链归档；
#: - raw：上游单文件构件（如 Tesseract `.traineddata` 语言包），显式声明才启用。
SUPPORTED_ARTIFACT_FORMATS = frozenset({"zip", "whl", "tar.gz", "raw"})

#: 支持的组件运行入口承载形态：
#: - native：独立可执行文件或数据入口（默认）；
#: - worker-wheelpack：由宿主 `omnicrawler-worker` 隔离子进程挂载已校验 Wheel-Pack 执行。
SUPPORTED_ENTRY_RUNNERS = frozenset({"native", "worker-wheelpack"})

#: 允许出现在 recipe 里的顶层键。未知键一律拒绝——
#: 否则一个 `post_install` 之类的"顺手加的功能"就能变成任意执行入口。
ALLOWED_KEYS = frozenset(
    {
        "schema",
        "name",
        "version",
        "description",
        "platforms",
        "architectures",
        "python_abi",
        # 该组件**提供**哪些能力（供插件声明所需能力时匹配）。
        # 例：`["ocr.paddle"]`。插件侧用 required_capabilities 声明需要什么，
        # 两者对上才能判断「装了没装」。
        "provides",
        # 组件种类：`wheel-pack`（自带 Python 运行时）/ `data`（纯数据资产，
        # 与平台和架构无关）/ `native`（原生可执行文件）。
        # 缺省按 `wheel-pack` 处理（向后兼容早期配方）。
        # **为什么要显式声明**：v1.0.14 曾把纯数据组件（Tesseract 语言包）
        # 误当 Wheel-Pack 去 `run`，报「缺少 site-packages」—— 靠
        # 「有没有 runner」推断类型是脆的，种类应当自己说出来。
        "kind",
        # ── Schema v2（§15.8）──
        # 依赖图：组件需要哪些包。与 `deps.lock.json` 的区别：
        # 后者是**解析后的闭包快照**（含传递依赖），本字段是**直接声明**。
        # 两者不重复：声明说「我要什么」，快照说「实际解析出什么」。
        "requires",
        # 每平台显式枚举（`{arch: {abi, artifacts...}}`）。
        # 取代 v1 的 `platform_architectures`。
        "platform_matrix",
        # 显式 id（可选）。声明后必须等于所在目录名 —— 由门禁校验。
        "id",
        # 冒烟声明：由**组件自己**说「我怎么算能跑」，验证器只负责执行与核对。
        # 见 `component_smoke.py`。缺省时 wheel-pack 会被判红（必须声明）。
        "smoke",
        # 过渡字段：`platform_architectures: {平台: [架构...]}`。
        # 全局 `architectures` 表达不了「某平台只支持部分架构」
        # （例：paddlepaddle 的 macOS 只有 arm64）。Schema v2 用
        # `platform_matrix` 取代它，届时删除本键。
        "platform_architectures",
        "artifacts",
        "entry",
    }
)

#: 每个 artifact 允许的键。
ALLOWED_ARTIFACT_KEYS = frozenset(
    {
        "url",
        "sha256",
        "size",
        "format",
        "filename",
        "extract_to",
        # 该构件适用于哪些平台。缺省 = 所有平台（向后兼容）。
        # 用于「同一份 recipe 在不同平台拉不同轮子」：如 paddle 的
        # win_amd64 / manylinux1_x86_64 / macosx_11_0_arm64。
        "platforms",
        "include",
        "strip_components",
        # 构件来源。缺省 `remote`（从 url 拉取）。
        #
        # `builtin` = **字节随包发布，不从网络拉**。用于「内置但也要被组件体系
        # 管理」的东西 —— 典型就是浏览器内核：415 MiB 的 Chromium 必须随包
        # 一起发（首启即用），但它同样需要「可列出、可切换、可校验」。
        #
        # 选 `builtin` 时**不写 url**，改为 `builtin_path`（相对安装根），
        # 并且 `sha256` 仍然必填 —— 「内置」不等于「免校验」：
        # 包内的文件同样可能在两次使用之间被替换或损坏。
        "source",
        "builtin_path",
    }
)

#: entry 允许的键。
#: entry 允许的键。
#:
#: `module`：入口在 `site-packages` 内的相对路径。**只对 `runner=worker-wheelpack`
#: 有意义** —— 此时 `entry.path` 指向装配器生成的声明文件，真正的入口是 `module`。
#: 声明的是**组件自身依赖**里的模块，不是本仓提供的脚本（与 asset_trust 的
#: 「recipe 不得含可执行钩子」不冲突：那是"不能带脚本进来"，这是"指个已有模块"）。
ALLOWED_ENTRY_KEYS = frozenset({
    "path",
    "protocol",
    "engine",
    "runner",
    "module",
    # 平台专属入口：`{"windows": "browser/firefox/firefox.exe", "linux": "..."}`。
    #
    # 有些组件的可执行文件在不同平台**不仅扩展名不同，目录结构也不同**：
    # Firefox 解压后是 `firefox/firefox`（Linux）与 `firefox/firefox.exe`（Windows），
    # WebKit 是 `Playwright.exe` 与 `pw_run.sh`。单一个 `path` 表达不了，
    # 而在 `platform_matrix` 里塞 `entry` 会被判「不允许的键」——
    # 所以给它一个显式的顶层字段。
    "path_by_platform",
})


class RecipeError(ValueError):
    """recipe 不合法，或装配无法安全进行。"""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RecipeError(message)


def validate_recipe(recipe: dict[str, Any]) -> dict[str, Any]:
    """校验 recipe 的结构与安全约束，返回原对象（便于链式使用）。

    **只做声明式校验，不触碰网络与磁盘。**
    """
    _require(isinstance(recipe, dict), "recipe 必须是 JSON 对象")

    unknown = set(recipe) - ALLOWED_KEYS
    _require(
        not unknown,
        f"recipe 含不允许的键：{sorted(unknown)}。"
        "recipe 必须是声明式的——可执行钩子（如 post_install）一律不允许。",
    )

    schema = recipe.get("schema")
    _require(
        schema in SUPPORTED_SCHEMA,
        f"不支持的 recipe schema 版本：{schema!r}（支持 {SUPPORTED_SCHEMA}）",
    )

    # 可选的显式 id：若声明，必须与所在目录名一致（由门禁校验）。
    # 不声明时由目录名决定（市场侧 build.py 用 parent.name）。
    declared_id = recipe.get("id")
    if declared_id is not None:
        id_ok = isinstance(declared_id, str) and bool(declared_id.strip())
        _require(id_ok, "recipe 的 id 若声明必须是非空字符串")

    for key in ("name", "version"):
        value = recipe.get(key)
        _require(
            bool(isinstance(value, str) and value.strip()),
            f"recipe 缺少 {key}",
        )

    for key in ("platforms", "architectures"):
        value = recipe.get(key)
        _require(
            bool(isinstance(value, list) and value and all(isinstance(v, str) for v in value)),
            f"recipe 的 {key} 必须是非空字符串列表",
        )

    artifacts = recipe.get("artifacts")
    _require(bool(isinstance(artifacts, list) and artifacts), "recipe 需要至少一个 artifact")
    assert isinstance(artifacts, list)  # 供类型检查收窄（上面已保证）
    for index, artifact in enumerate(artifacts):
        _validate_artifact(artifact, index)

    _validate_smoke(recipe)
    _validate_kind(recipe)
    _validate_provides(recipe)
    # v2 的 platform_matrix 与 v1 的 platform_architectures 互斥，
    # 由 _validate_platform_matrix 内部强制（两者同时出现即拒）。
    _validate_entry_paths(recipe)
    _validate_platform_matrix(recipe)
    _validate_requires(recipe)
    _validate_platform_architectures(recipe, artifacts)

    # 每个平台都必须至少有一个适用构件 —— 否则装上却缺文件，错在装配期而非下载期。
    declared_platforms = [str(p) for p in recipe["platforms"]]
    for platform_name in declared_platforms:
        applicable = artifacts_for_platform(recipe, platform_name)
        _require(
            bool(applicable),
            f"recipe 声明支持 {platform_name}，但没有任何 artifact 适用于它。"
            "请给该平台的构件标注 platforms，或从 platforms 里移除该平台。",
        )

    # ★ 更进一步：**平台专属构件**存在时，每个平台都必须有各自的专属构件。
    #
    # 否则「一个 windows 专属轮子 + 一个纯 Python 通用包」会让 linux/macos
    # 也"有适用构件"（靠那个通用包），掩盖了它们缺少平台专属二进制的真相 ——
    # 实测踩到：把 ocr-paddle 的 linux 轮子删掉，校验仍然通过。
    #
    # 不误伤纯数据组件：tesseract 语言包只有一个通用构件、**没有任何专属构件**，
    # 此时不触发本规则。
    platform_specific = [
        platform
        for artifact in artifacts
        if isinstance(artifact.get("platforms"), (list, str))
        for platform in (
            [artifact["platforms"]]
            if isinstance(artifact["platforms"], str)
            else artifact["platforms"]
        )
    ]
    if platform_specific:
        specific_by_platform: dict[str, list[dict[str, Any]]] = {p: [] for p in declared_platforms}
        for artifact in artifacts:
            declared = artifact.get("platforms")
            if not isinstance(declared, (list, str)):
                continue
            names = [declared] if isinstance(declared, str) else declared
            for name in names:
                specific_by_platform.setdefault(str(name), []).append(artifact)
        for platform_name in declared_platforms:
            _require(
                bool(specific_by_platform.get(platform_name)),
                f"recipe 声明支持 {platform_name}，但它只有通用构件、没有平台专属构件。"
                "多平台配方应给每个平台各自的二进制；若该组件确实是纯数据，"
                "请把构件的 platforms 标注去掉（表示全平台通用）。",
            )

    entry = recipe.get("entry")
    _require(isinstance(entry, dict), "recipe 需要 entry（入口声明）")
    assert isinstance(entry, dict)  # 供类型检查收窄
    _require(
        set(entry) <= ALLOWED_ENTRY_KEYS,
        f"entry 含不允许的键：{sorted(set(entry) - ALLOWED_ENTRY_KEYS)}",
    )
    # `path` 与 `path_by_platform` 二选一：
    # 后者用于「可执行文件在不同平台结构不同」的组件（浏览器内核）。
    if entry.get("path_by_platform") is None:
        path = entry.get("path")
        _require(bool(isinstance(path, str) and path.strip()), "entry.path 不能为空")
        assert isinstance(path, str)
        _safe_relative(path)
    runner = entry.get("runner")
    if runner is not None:
        _require(
            isinstance(runner, str) and runner in SUPPORTED_ENTRY_RUNNERS,
            f"entry.runner 不受支持：{runner!r}（仅支持 {sorted(SUPPORTED_ENTRY_RUNNERS)}）",
        )

    return recipe


#: 支持的组件种类。
COMPONENT_KINDS = ("wheel-pack", "data", "native")

#: 缺省种类（早期配方没有这个字段，按最严格的一类处理）。
DEFAULT_COMPONENT_KIND = "wheel-pack"


def component_kind(recipe: dict[str, Any]) -> str:
    """取组件种类；缺省 `wheel-pack`。"""
    value = str(recipe.get("kind") or DEFAULT_COMPONENT_KIND).strip().lower()
    return value or DEFAULT_COMPONENT_KIND


def _validate_smoke(recipe: dict[str, Any]) -> None:
    """校验 `smoke` 声明（委托给 `component_smoke.parse_smoke`）。"""
    from .component_smoke import SmokeSpecError, parse_smoke

    try:
        parse_smoke(recipe)
    except SmokeSpecError as exc:
        _require(False, str(exc))


def _validate_kind(recipe: dict[str, Any]) -> None:
    """校验 `kind`，并检查它与配方其余部分自洽。

    自洽判据（缺一即红）：

    1. `kind` 必须是 `COMPONENT_KINDS` 之一；
    2. `data` 类不得声明 `entry.runner`（纯数据没有可执行入口）；
    3. `wheel-pack` 类**必须**声明 `entry.runner`（否则无从执行）。
    """
    kind = component_kind(recipe)
    _require(
        kind in COMPONENT_KINDS,
        f"不支持的组件种类：{kind!r}（仅支持 {list(COMPONENT_KINDS)}）",
    )
    entry = recipe.get("entry") or {}
    assert isinstance(entry, dict)
    runner = entry.get("runner")
    if kind == "data":
        _require(
            not runner,
            "kind=data 的组件不得声明 entry.runner —— 纯数据没有可执行入口",
        )
    elif kind == "wheel-pack":
        _require(
            bool(runner),
            "kind=wheel-pack 的组件必须声明 entry.runner（缺省应写 worker-wheelpack）",
        )


def _validate_provides(recipe: dict[str, Any]) -> None:
    """校验 `provides`：组件对外声明提供哪些能力。

    **为什么要它**：插件用 `required_capabilities` 声明「我需要 `ocr.paddle`」，
    而组件此前**没有任何字段说「我提供 `ocr.paddle`」** —— 两边接不上，
    只能靠人脑对应。声明 `provides` 后，市场可以校验
    「每个插件的必需能力都被某个组件覆盖」，宿主也能提示「装 X 组件可解锁此功能」。

    命名约定：小写、点分、无空格（如 `ocr.paddle`）。**不做语义校验** ——
    能力名是生态约定的字符串，宿主不解释它。
    """
    provides = recipe.get("provides")
    if provides is None:
        return
    ok = (
        isinstance(provides, list)
        and bool(provides)
        and all(isinstance(item, str) and item.strip() for item in provides)
    )
    _require(ok, "provides 必须是非空字符串列表（如 ocr.paddle 这种点分能力名）")
    assert isinstance(provides, list)
    normalized = [item.strip().lower() for item in provides]
    _require(
        len(set(normalized)) == len(normalized),
        f"provides 含重复项：{normalized}",
    )
    for item in normalized:
        _require(
            all(ch.isalnum() or ch in "._-" for ch in item),
            f"provides 的能力名只允许字母/数字/._-：{item!r}",
        )


def _validate_platform_architectures(recipe: dict[str, Any], artifacts: list[Any]) -> None:
    """校验过渡字段 `platform_architectures`，并**从构件字节反推比对**。

    这个字段存在的唯一理由：全局 `architectures` 表达不了「某平台只支持部分架构」。
    实测例：`paddlepaddle 3.3.1` 的 macOS 轮子只有 arm64，Intel Mac 装不了。

    **判据（缺一即红）**：

    1. 键必须是已声明的平台，不得引入新平台；
    2. 值必须是非空字符串列表，且每一项都是能识别的架构名；
    3. 每个 `(平台, 架构)` 组合，**必须能从该平台的构件文件名推出对应架构**
       —— 也就是说，不允许「声明了某个架构、却没有那个架构的构件」；
    4. 全局 `architectures` 必须等于各平台声明的**并集**（防两处漂移）。
    """
    declared = recipe.get("platform_architectures")
    if declared is None:
        return
    ok = isinstance(declared, dict) and bool(declared)
    _require(ok, "platform_architectures 必须是非空对象（平台 → 架构列表）")
    assert isinstance(declared, dict)

    platforms = {str(p) for p in recipe["platforms"]}
    unknown_platforms = set(declared) - platforms
    _require(
        not unknown_platforms,
        f"platform_architectures 含未声明的平台：{sorted(unknown_platforms)}",
    )

    union: set[str] = set()
    for platform, archs in declared.items():
        archs_ok = (
            isinstance(archs, list)
            and bool(archs)
            and all(isinstance(a, str) and a.strip() for a in archs)
        )
        _require(archs_ok, f"platform_architectures[{platform}] 必须是非空字符串列表")
        normalized = {normalize_architecture(a) for a in archs}
        union |= normalized

        # **内置构件豁免反推**：`source: builtin` 的构件是一整个目录
        # （浏览器内核有 310 个文件），文件名里不会有 `win_amd64` 这类标记。
        # 它如实记录了**每个文件**的 sha256 与大小（`files.lock.json`），
        # 推导依据比文件名更精确 —— 所以这里跳过文件名反推，不跳过校验。
        platform_artifacts = artifacts_for_platform(recipe, platform)
        if platform_artifacts and all(
            str(a.get("source") or "remote").lower() == "builtin"
            for a in platform_artifacts
        ):
            union |= normalized
            continue

        # 反推：该平台构件的文件名里能认出哪些架构
        available: set[str] = set()
        for artifact in artifacts_for_platform(recipe, platform):
            filename = str(artifact.get("url") or "").rsplit("/", 1)[-1].lower()
            for token, canonical in (
                ("aarch64", "aarch64"),
                ("arm64", "aarch64"),
                ("universal2", "aarch64"),  # universal2 同时覆盖两种
                ("win_amd64", "x86_64"),
                ("x86_64", "x86_64"),
                ("amd64", "x86_64"),
            ):
                if token in filename:
                    available.add(canonical)
                    if token == "universal2":
                        available.add("x86_64")
        missing = normalized - available
        _require(
            not missing,
            f"platform_architectures[{platform}] 声明了 {sorted(missing)}，"
            f"但该平台没有任何对应架构的构件 —— 声明必须能从构件字节推出。",
        )

    declared_union = {normalize_architecture(a) for a in recipe["architectures"]}
    _require(
        declared_union == union,
        f"architectures 与 platform_architectures 的并集不一致："
        f"全局 {sorted(declared_union)} vs 并集 {sorted(union)}。两处必须同时维护。",
    )


#: `platform_matrix[平台]` 允许的键 —— 键是**架构名**，值为该架构的明细。
_PLATFORM_MATRIX_DETAIL_KEYS = frozenset({"abi", "artifacts"})


def _validate_platform_matrix(recipe: dict[str, Any]) -> None:
    """校验 Schema v2 的 `platform_matrix`。

    形态::

        "platform_matrix": {
            "windows": {"x86_64": {"abi": ["cp313"], "artifacts": [0, 3, 7]}},
            "macos":   {"aarch64": {"abi": ["cp313"], "artifacts": [1, 4]}}
        }

    `artifacts` 是该架构用到的**构件下标**。这样一个平台可以只支持部分架构
    （`paddlepaddle 3.3.1` 的 macOS 只有 arm64），而 v1 的
    `platform_architectures` 只能表达「平台 → 架构列表」，
    表达不了「同平台不同架构用不同构件/ABI」。

    **与 v1 过渡字段互斥**：不允许双写，否则两处必然漂移。
    """
    matrix = recipe.get("platform_matrix")
    legacy = recipe.get("platform_architectures")
    _require(
        not (matrix is not None and legacy is not None),
        "platform_matrix 与 platform_architectures 不能同时出现 —— "
        "v2 已用 matrix 取代过渡字段，不保留双写",
    )
    if matrix is None:
        return

    ok = isinstance(matrix, dict) and bool(matrix)
    _require(ok, "platform_matrix 必须是非空对象（平台 → 架构 → 明细）")
    assert isinstance(matrix, dict)

    platforms = {str(p) for p in recipe["platforms"]}
    unknown = set(matrix) - platforms
    _require(
        not unknown,
        f"platform_matrix 含未声明的平台：{sorted(unknown)}",
    )

    artifacts = recipe.get("artifacts") or []
    union: set[str] = set()
    for platform, per_arch in matrix.items():
        _require(
            isinstance(per_arch, dict) and bool(per_arch),
            f"platform_matrix[{platform}] 必须是非空对象（架构 → 明细）",
        )
        for arch, detail in per_arch.items():
            arch_ok = isinstance(arch, str) and bool(arch.strip())
            _require(
                arch_ok,
                f"platform_matrix[{platform}] 的架构名必须是非空字符串",
            )
            union.add(normalize_architecture(arch))
            _require(
                isinstance(detail, dict),
                f"platform_matrix[{platform}][{arch}] 的明细必须是对象",
            )
            unknown_keys = set(detail) - _PLATFORM_MATRIX_DETAIL_KEYS
            _require(
                not unknown_keys,
                f"platform_matrix[{platform}][{arch}] 含不允许的键："
                f"{sorted(unknown_keys)}（只允许 {sorted(_PLATFORM_MATRIX_DETAIL_KEYS)}）",
            )
            indexes = detail.get("artifacts")
            if indexes is not None:
                ok_idx = (
                    isinstance(indexes, list)
                    and bool(indexes)
                    and all(isinstance(i, int) and 0 <= i < len(artifacts) for i in indexes)
                )
                _require(
                    ok_idx,
                    f"platform_matrix[{platform}][{arch}].artifacts 必须是"
                    f"合法下标列表（0..{len(artifacts) - 1}）",
                )

    # 全局 architectures 必须等于 matrix 的并集（防两处漂移）
    declared = {normalize_architecture(a) for a in recipe["architectures"]}
    _require(
        declared == union,
        f"architectures 与 platform_matrix 的并集不一致："
        f"全局 {sorted(declared)} vs 并集 {sorted(union)}。两处必须同时维护。",
    )


def _validate_entry_paths(recipe: dict[str, Any]) -> None:
    """校验 `entry.path` 与 `entry.path_by_platform`。

    两者**至少有一个**：单平台组件用 `path` 就够；可执行文件在不同平台
    结构不同的（浏览器内核）用 `path_by_platform` 逐平台声明。
    """
    entry = recipe.get("entry") or {}
    by_platform = entry.get("path_by_platform")
    if by_platform is None:
        _require(
            isinstance(entry.get("path"), str) and bool(str(entry.get("path")).strip()),
            "entry 必须有 path（或改用 path_by_platform 逐平台声明）",
        )
        return

    ok = isinstance(by_platform, dict) and bool(by_platform)
    _require(ok, "entry.path_by_platform 必须是非空对象（平台 → 入口路径）")
    assert isinstance(by_platform, dict)

    platforms = {str(p) for p in recipe["platforms"]}
    unknown = set(by_platform) - platforms
    _require(
        not unknown,
        f"entry.path_by_platform 含未声明的平台：{sorted(unknown)}",
    )
    # **每个声明平台都要有入口**，缺了装配时就不知道该启动什么
    missing = platforms - set(by_platform)
    _require(
        not missing,
        f"entry.path_by_platform 缺少这些平台的入口：{sorted(missing)}",
    )
    for platform, value in by_platform.items():
        value_ok = isinstance(value, str) and bool(value.strip())
        _require(
            value_ok,
            f"entry.path_by_platform[{platform}] 必须是非空字符串",
        )
        _safe_relative(str(value))


def _validate_requires(recipe: dict[str, Any]) -> None:
    """校验 Schema v2 的 `requires`（依赖图声明）。

    形态：包名列表，或 `{包名: 版本约束}`。**只接受字符串或字符串列表** ——
    不接受可执行内容，与「配方必须是声明式的」同一条纪律。

    与 `deps.lock.json` 的分工：

    | | 表达什么 | 谁维护 |
    |---|---|---|
    | `requires` | 组件**直接**依赖什么 | 与配方一起手写/签名 |
    | `deps.lock.json` | 实际解析出的**闭包**（含传递依赖） | 从 PyPI 生成 |

    两者都要有：只有闭包说不出「为什么需要它」，只有声明说不出「实际解析成什么」。
    """
    requires = recipe.get("requires")
    if requires is None:
        return

    if isinstance(requires, str):
        requires = [requires]
    # 两种形态都合法：列表（只要包名）或对象（包名 → 版本约束）
    ok = (isinstance(requires, list) and bool(requires)) or (
        isinstance(requires, dict) and bool(requires)
    )
    _require(
        ok,
        "requires 必须是非空字符串列表（或 {包名: 版本约束} 的对象）",
    )

    if isinstance(requires, dict):
        items = list(requires)
        bad = [
            name
            for name, constraint in requires.items()
            if not isinstance(name, str) or not name.strip() or not isinstance(constraint, str)
        ]
        _require(
            not bad,
            f"requires 的 {bad} 项必须写成 {{包名: 版本约束字符串}}",
        )
        names = [str(n).strip() for n in items]
    else:
        bad_items = [r for r in requires if not isinstance(r, str) or not r.strip()]
        _require(not bad_items, f"requires 含非字符串项：{bad_items}")
        names = [str(r).strip() for r in requires]

    # 包名归一后判重：`Foo_Bar` 与 `foo-bar` 是同一个包
    normalized = [re.sub(r"[-_.]+", "_", n).lower() for n in names]
    _require(
        len(set(normalized)) == len(normalized),
        f"requires 含重复包（归一后）：{sorted(n for n in normalized if normalized.count(n) > 1)}",
    )

    # 必须是合法包名，且不能是 urllib 式的带路径/URL 形态
    for name in names:
        _require(
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name) is not None,
            f"requires 的 {name!r} 不是合法包名",
        )


def artifacts_for_platform(recipe: dict[str, Any], platform: str) -> list[dict[str, Any]]:
    """该平台需要拉取的构件。

    构件的 `platforms` 缺省表示「所有平台」（向后兼容早期只有 windows 的配方）。
    显式声明时按成员判定 —— 这样一份 recipe 可以给不同平台配不同轮子。
    """
    selected: list[dict[str, Any]] = []
    for artifact in recipe.get("artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        declared = artifact.get("platforms")
        if declared is None:
            selected.append(artifact)
            continue
        if isinstance(declared, str):
            declared = [declared]
        if platform in {str(item) for item in declared}:
            selected.append(artifact)
    return selected


def infer_artifact_format(artifact: dict[str, Any]) -> str:
    """解析构件格式；`raw` 必须显式声明，归档可按 URL 后缀确定性推断。"""
    explicit = artifact.get("format")
    if explicit is not None:
        _require(
            isinstance(explicit, str) and explicit in SUPPORTED_ARTIFACT_FORMATS,
            f"不支持的构件 format：{explicit!r}（仅支持 {sorted(SUPPORTED_ARTIFACT_FORMATS)}）",
        )
        return str(explicit)
    url_path = urlsplit(str(artifact.get("url", ""))).path.lower()
    if url_path.endswith((".tar.gz", ".tgz")):
        return "tar.gz"
    if url_path.endswith(".whl"):
        return "whl"
    return "zip"


def _resolve_raw_relative_path(extract_to: str, filename: str | None) -> PurePosixPath:
    """解析单文件构件相对落盘路径，确保不为空、不含目录穿越或驱动器前缀。"""
    if filename is not None:
        _require(isinstance(filename, str) and bool(filename.strip()), "raw 构件的 filename 不能为空")
        assert isinstance(filename, str)
        clean_name = _safe_relative(filename)
        _require(
            len(clean_name.parts) == 1 and clean_name.name not in {".", ".."},
            f"raw 构件的 filename 必须是单个安全文件名：{filename!r}",
        )
        if extract_to:
            return _safe_relative(extract_to) / clean_name
        return clean_name
    _require(
        bool(extract_to and extract_to.strip()) and not extract_to.endswith(("/", "\\")),
        "raw 单文件构件必须通过 filename 或 extract_to 指定相对文件落盘路径",
    )
    return _safe_relative(extract_to)


def _validate_artifact(artifact: Any, index: int) -> None:
    _require(isinstance(artifact, dict), f"artifact #{index} 必须是对象")

    unknown = set(artifact) - ALLOWED_ARTIFACT_KEYS
    _require(not unknown, f"artifact #{index} 含不允许的键：{sorted(unknown)}")

    # 来源：`remote`（默认，从 url 拉）或 `builtin`（字节随包）。
    source = str(artifact.get("source") or "remote").strip().lower()
    _require(
        source in ("remote", "builtin"),
        f"artifact #{index} 的 source 只能是 remote 或 builtin，实际 {source!r}",
    )

    if source == "builtin":
        # 内置构件：**不写 url**，写 `builtin_path`（相对安装根）。
        # sha256 仍然必填 —— 「内置」不等于「免校验」。
        _require(
            artifact.get("url") in (None, ""),
            f"artifact #{index} 声明了 source=builtin 就不能再写 url",
        )
        builtin_path = artifact.get("builtin_path")
        builtin_ok = isinstance(builtin_path, str) and bool(builtin_path.strip())
        _require(
            builtin_ok,
            f"artifact #{index} 声明了 source=builtin 就必须写 builtin_path",
        )
        _safe_relative(builtin_path)
    else:
        _require(
            artifact.get("builtin_path") in (None, ""),
            f"artifact #{index} 未声明 source=builtin，不应写 builtin_path",
        )
        url = artifact.get("url")
        _require(
            isinstance(url, str) and url.startswith(("https://", "http://")),
            f"artifact #{index} 的 url 必须是 http(s) 地址",
        )

    digest = artifact.get("sha256")
    _require(
        isinstance(digest, str) and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest),
        f"artifact #{index} 缺少合法的 sha256 —— 没有哈希就无法校验来源，拒绝整份 recipe（不做部分信任）",
    )

    fmt = infer_artifact_format(artifact)

    extract_to = artifact.get("extract_to", "")
    _require(isinstance(extract_to, str), f"artifact #{index} 的 extract_to 必须是字符串")
    if extract_to:
        _safe_relative(extract_to)

    filename = artifact.get("filename")
    if fmt == "raw":
        _resolve_raw_relative_path(extract_to, filename)
        _require(
            artifact.get("include") is None and artifact.get("strip_components") is None,
            f"artifact #{index} 为 raw 单文件构件，不支持 include 或 strip_components",
        )
    else:
        _require(
            filename is None,
            f"artifact #{index} 仅在 format='raw' 时允许指定 filename",
        )

    size = artifact.get("size")
    if size is not None:
        _require(
            type(size) is int and size >= 0,
            f"artifact #{index} 的 size 必须是非负整数",
        )

    strip_components = artifact.get("strip_components")
    if strip_components is not None:
        _require(
            type(strip_components) is int and strip_components >= 0,
            f"artifact #{index} 的 strip_components 必须是非负整数",
        )

    include = artifact.get("include")
    if include is not None:
        _require(
            isinstance(include, list) and all(isinstance(item, str) for item in include),
            f"artifact #{index} 的 include 必须是字符串列表",
        )
        for item in include:
            _safe_relative(item)


def _safe_relative(value: str) -> PurePosixPath:
    """拒绝绝对路径、Windows 盘符前缀与 `..`（路径穿越）。"""
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    _require(bool(normalized) and bool(path.parts), f"路径不能为空：{value!r}")
    _require(not path.is_absolute(), f"不允许绝对路径：{value!r}")
    _require(":" not in path.parts[0], f"不允许驱动器前缀路径：{value!r}")
    _require(".." not in path.parts and "." not in path.parts, f"不允许路径穿越（.. / .）：{value!r}")
    return path


def load_recipe(path: Path) -> dict[str, Any]:
    """从文件读取并校验 recipe。

    **只接受 .json**：`.sh/.bat/.py/.exe/.ps1` 一律拒绝——recipe 是数据，不是程序。
    """
    path = Path(path)
    _require(
        path.suffix.lower() in ALLOWED_RECIPE_SUFFIXES,
        f"recipe 必须是声明式 JSON（{', '.join(ALLOWED_RECIPE_SUFFIXES)}）；"
        f"拒绝 {path.suffix or '无扩展名'} —— 不允许可执行脚本作为 recipe",
    )
    _require(path.is_file(), f"recipe 文件不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RecipeError(f"recipe 不是合法 JSON：{exc}") from exc
    return validate_recipe(data)


def recipe_digest(recipe: dict[str, Any]) -> str:
    """recipe 的稳定摘要（规范化序列化后 sha256）。

    用途：把"装配方式"本身纳入签名清单——recipe 被改等价于组件来源被改。
    """
    canonical = json.dumps(recipe, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def current_abi() -> str:
    """当前解释器的扩展 ABI 标志（如 ``cp312``）。

    注意：**冻结包运行时的 ABI 由构建解释器决定**，与用户机器无关。
    本函数读 ``sys.implementation``，在冻结环境中同样返回构建时的取值。
    """
    implementation = sys.implementation
    version = implementation.version
    return f"{implementation.name[:2]}{version.major}{version.minor}"


def current_platform() -> str:
    """平台名（与 recipe 的 platforms 对齐）。"""
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


#: 架构名归一表。**别名收敛到同一规范名**。
#:
#: `arm64` 是 Apple 的说法、`aarch64` 是 Linux/GNU 的说法，两者是**同一个架构**。
#: 早期 `current_architecture()` 返回 `aarch64`，而配方里写 `arm64`
#: （`tesseract-chi-sim` 就是这样），精确匹配会判「不支持当前架构」——
#: 明明支持却装不上。所以比对前两侧都要过这张表。
_ARCHITECTURE_ALIASES = {
    "amd64": "x86_64",
    "x86_64": "x86_64",
    "x64": "x86_64",
    "i386": "x86",
    "i686": "x86",
    "x86": "x86",
    "arm64": "aarch64",
    "aarch64": "aarch64",
    "armv8": "aarch64",
    "universal2": "universal2",
}


def normalize_architecture(name: str) -> str:
    """把架构别名收敛到规范名。未知名字原样返回（不猜）。"""
    lowered = str(name).strip().lower()
    return _ARCHITECTURE_ALIASES.get(lowered, lowered)


def current_architecture() -> str:
    """架构名（规范名，与 recipe 的 architectures 归一后对齐）。"""
    import platform

    return normalize_architecture(platform.machine())


def check_compatibility(
    recipe: dict[str, Any],
    *,
    platform: str | None = None,
    architecture: str | None = None,
    abi: str | None = None,
) -> None:
    """在**装配前**确认本机与 recipe 匹配；不匹配则拒绝。"""
    target_platform = platform or current_platform()
    target_arch = architecture or current_architecture()
    target_abi = abi or current_abi()

    platforms = recipe.get("platforms") or []
    # **平台名也归一**：`darwin` 与 `macos` 是同一平台的两种叫法。
    #
    # 架构那边早就有这层归一（`arm64` = `aarch64`），平台这边漏了。
    # 配方作者自然会写 `darwin`（那是 Python `sys.platform` 的值），
    # 而 `current_platform()` 返回 `macos` ⇒ 明明支持却被判「不支持当前平台」。
    # 2026-11-22 CI macOS 实测因此红了多条。
    _PLATFORM_ALIASES = {"darwin": "macos", "osx": "macos", "win32": "windows"}
    normalized_platforms = {_PLATFORM_ALIASES.get(str(x).strip().lower(), str(x).strip().lower()) for x in platforms}
    normalized_target = _PLATFORM_ALIASES.get(target_platform, target_platform)
    _require(
        normalized_target in normalized_platforms,
        f"此组件不支持当前平台：需要 {platforms}，当前 {target_platform}",
    )

    architectures = recipe.get("architectures") or []
    # **归一后比对**：`arm64` 与 `aarch64` 是同一架构的两种叫法。
    # 精确字符串匹配会把「明明支持」判成不支持（`tesseract-chi-sim` 曾用 `arm64`）。
    accepted_archs = {normalize_architecture(a) for a in architectures}
    _require(
        normalize_architecture(target_arch) in accepted_archs,
        f"此组件不支持当前架构：需要 {architectures}，当前 {target_arch}",
    )

    required_abi = recipe.get("python_abi")
    if required_abi:
        # `python_abi` 是**列表**（一份 recipe 可以声明多个可接受的 ABI）。
        # 早先写成 `str(required_abi) == target_abi`，把 `["cp313"]` 变成
        # 字符串 `"['cp313']"` 再比较，与当前 ABI 永远不相等 —— 连"完全匹配"
        # 也会被判成不符（真实 CI 实测：需要 ['cp313']，当前 cp313，仍被拒）。
        accepted = (
            [required_abi] if isinstance(required_abi, str) else list(required_abi)
        )
        _require(
            target_abi in accepted,
            f"组件要求的 Python ABI 与当前不符：需要 {accepted}，当前 {target_abi}。"
            "跨 ABI 的二进制扩展无法加载，装上也用不了。",
        )


def _strip_relative_components(relative: PurePosixPath, count: int) -> PurePosixPath | None:
    if count <= 0:
        return relative
    if len(relative.parts) <= count:
        return None
    return PurePosixPath(*relative.parts[count:])


def _place_raw_artifact(
    source_file: Path,
    target_destination: Path,
) -> None:
    target_destination.parent.mkdir(parents=True, exist_ok=True)
    temp_name = target_destination.with_name(f".{target_destination.name}.{os.getpid()}.tmp")
    try:
        with source_file.open("rb") as src, temp_name.open("xb") as dst:
            while chunk := src.read(1024 * 1024):
                dst.write(chunk)
        os.replace(temp_name, target_destination)
    except Exception:
        temp_name.unlink(missing_ok=True)
        raise


def _extract_tar_member(
    tf: tarfile.TarFile,
    info: tarfile.TarInfo,
    destination: Path,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_name = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    source = tf.extractfile(info)
    if source is None:
        return
    written = 0
    try:
        with source, temp_name.open("xb") as target:
            while chunk := source.read(1024 * 1024):
                written += len(chunk)
                if written > DEFAULT_ZIP_READ_LIMITS.max_file_bytes:
                    raise UnsafePackageError(f"package member exceeds max size: {info.name!r}")
                target.write(chunk)
        if written != info.size:
            raise UnsafePackageError(f"package member size mismatch: {info.name!r}")
        mode = info.mode & 0o777 if info.mode else None
        if mode:
            try:
                temp_name.chmod(mode)
            except OSError:
                pass
        os.replace(temp_name, destination)
    except Exception:
        temp_name.unlink(missing_ok=True)
        raise


def _validate_and_select_tar(
    tf: tarfile.TarFile,
    wanted: set[str] | None,
    strip_count: int = 0,
) -> list[tuple[tarfile.TarInfo, PurePosixPath]]:
    infos = tf.getmembers()
    if len(infos) > DEFAULT_ZIP_READ_LIMITS.max_entries:
        raise UnsafePackageError(f"package contains more than {DEFAULT_ZIP_READ_LIMITS.max_entries} entries")

    selected: list[tuple[tarfile.TarInfo, PurePosixPath]] = []
    total = 0
    seen: set[str] = set()

    for info in infos:
        if info.isdir():
            continue
        if not (info.isreg() or info.isfile()):
            raise UnsafePackageError(f"package links or special files are not supported: {info.name!r}")
        if info.size < 0 or info.size > DEFAULT_ZIP_READ_LIMITS.max_file_bytes:
            raise UnsafePackageError(f"package member is too large: {info.name!r}")
        total += info.size
        if total > DEFAULT_ZIP_READ_LIMITS.max_total_bytes:
            raise UnsafePackageError("package exceeds the total uncompressed size limit")

        relative = _safe_relative(info.name)
        if strip_count > 0:
            stripped = _strip_relative_components(relative, strip_count)
            if stripped is None:
                continue
            relative = stripped

        name = relative.as_posix()
        key = name.casefold()
        if key in seen:
            raise UnsafePackageError(f"package contains a duplicate path: {info.name!r}")
        seen.add(key)

        if wanted is not None and name not in wanted:
            continue
        selected.append((info, relative))

    return selected


def extract_artifact(
    archive: Path,
    target: Path,
    *,
    expected_sha256: str | None = None,
    include: Iterable[str] | None = None,
    artifact_format: str | None = None,
    filename: str | None = None,
    strip_components: int = 0,
) -> None:
    """把构件解压/落盘到 ``target``，**先校验后落盘**。

    支持格式：
    - `zip` / `whl`：经 `archive_security` 流式校验解压；
    - `tar.gz`：经流式边界校验解压；
    - `raw`：校验单文件哈希并原子落地到目标文件位置。

    :param expected_sha256: 期望的归档哈希。为 None 时**不做哈希校验**
        （只应在测试或调用方已自行校验时使用）。
    :param include: 只解压这些相对路径；None 表示全部。
    :param artifact_format: 构件格式（zip / whl / tar.gz / raw）；None 自动探测。
    :param filename: 当 format='raw' 且 target 为目录时，指定单文件名称。
    :param strip_components: 解压归档时剥离前 N 层目录。

    **两阶段**：先在临时目录/文件完成校验与提取，成功后才原子替换到 ``target``。
    任何失败都不会留下半装状态。
    """
    import tempfile

    archive = Path(archive)
    target = Path(target)
    _require(archive.is_file(), f"构件不存在：{archive}")

    if expected_sha256 is not None:
        # 流式算哈希：构件可能是几十 MB，不要整个读进内存。
        digest = hashlib.sha256()
        with archive.open("rb") as handle:
            while block := handle.read(1024 * 1024):
                digest.update(block)
        actual = digest.hexdigest()
        _require(
            actual == expected_sha256,
            f"构件哈希不符：期望 {expected_sha256[:16]}…，实际 {actual[:16]}… —— 拒绝落盘",
        )

    fmt = artifact_format
    if fmt is None:
        if tarfile.is_tarfile(archive):
            fmt = "tar.gz"
        else:
            fmt = "zip"

    if fmt == "raw":
        destination = target / filename if filename else target
        _place_raw_artifact(archive, destination)
        return

    wanted = None
    if include is not None:
        wanted = {str(_safe_relative(item)) for item in include}

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent, prefix=".assemble-") as staging:
        staging_path = Path(staging)
        try:
            if fmt == "tar.gz":
                with tarfile.open(archive, "r:*") as tf:
                    tar_resolved = _validate_and_select_tar(tf, wanted, strip_count=strip_components)
                    for info, relative in tar_resolved:
                        destination = staging_path / relative
                        _extract_tar_member(tf, info, destination)
            else:
                with zipfile.ZipFile(archive) as zf:
                    resolved = _validate_and_select(zf, wanted, strip_count=strip_components)
                    for info, relative in resolved:
                        # **跳过 `.pth` 文件**：它们由**安装器**（pip）在解释器
                        # 启动时执行，解压出来会产生副作用。
                        #
                        # 实测后果：setuptools 自带的 `distutils-precedence.pth`
                        # 会把 `_distutils_hack` 注入，令宿主/系统的 distutils
                        # 优先于组件 site-packages 里的那份，于是
                        # `import setuptools.command.easy_install` 失败
                        # （CI 报 `No module named 'setuptools.command.easy_install'`，
                        # 而文件明明就在那里）。
                        #
                        # 我方装配是「纯解压、零安装动作」，`.pth` 属于安装语义，
                        # 不在范围内。
                        if relative.suffix == ".pth":
                            continue
                        destination = staging_path / relative
                        copy_zip_member(zf, info, destination)
        except UnsafePackageError as exc:
            raise RecipeError(f"构件包不安全，已拒绝装配：{exc}") from exc

        # 全部成功后才落到 target。
        #
        # **target 已存在时必须合并，不能删掉重建**：
        # 一份 recipe 常有多个 `extract_to` 相同的构件（如 ocr-paddle 的
        # 17 个 wheel 全部解到 `site-packages/`）。早先这里 `rmtree(target)`
        # 再 `replace()`，于是**后一个构件会把前一个解出来的内容整目录删掉** ——
        # 最后一个 win 的只剩自己，`paddleocr/` 就这样消失了，
        # 装完后报「组件入口不存在：site-packages/paddleocr/__init__.py」
        # （真实 CI 踩到）。
        #
        # 合并语义与 pip 解 wheel 到 site-packages 一致：同路径文件后写覆盖，
        # 不同路径各自保留。
        if target.exists():

            for item in staging_path.iterdir():
                destination_item = target / item.name
                if item.is_dir() and destination_item.is_dir():
                    shutil.copytree(item, destination_item, dirs_exist_ok=True)
                else:
                    if destination_item.exists():
                        if destination_item.is_dir():
                            shutil.rmtree(destination_item)
                        else:
                            destination_item.unlink()
                    shutil.move(str(item), str(destination_item))
        else:
            staging_path.replace(target)


def _validate_and_select(
    zf: zipfile.ZipFile,
    wanted: set[str] | None,
    strip_count: int = 0,
) -> list[tuple[Any, PurePosixPath]]:
    """逐成员做路径安全检查，并按 include 过滤与 strip_components 剥离。"""
    selected: list[tuple[Any, PurePosixPath]] = []
    seen: set[str] = set()
    for info in zf.infolist():
        if info.is_dir():
            continue
        relative = _safe_relative(info.filename)
        if strip_count > 0:
            stripped = _strip_relative_components(relative, strip_count)
            if stripped is None:
                continue
            relative = stripped

        name = relative.as_posix()
        key = name.casefold()
        if key in seen:
            raise UnsafePackageError(f"package contains a duplicate path: {info.filename!r}")
        seen.add(key)

        if wanted is not None and name not in wanted:
            continue
        selected.append((info, relative))
    return selected


def _resolve_builtin_source(builtin_path: str) -> Path:
    """把配方里的 `builtin_path` 解析成安装根下的绝对路径。

    **必须防目录穿越**：`builtin_path` 来自市场配方（第三方可写），
    若允许 `../../etc/passwd`，一个恶意配方就能让宿主去校验任意文件。

    解析基准是**安装根**（`install_root()`）：内置构件随包发布，
    相对安装根定位，与 `runtime_paths.browsers_root()` 同源。
    """
    from ..core.runtime_paths import browsers_root, install_root

    relative = _safe_relative(builtin_path)
    root = Path(install_root()).resolve()

    # **两种部署形态**：
    #   * 冻结产物：`<install_root>/browsers/...`（随包发布）
    #   * 开发工作区：`<install_root>/.runtime/browsers/...`
    #     （`browsers_root()` 的放法，`prepare_*_runtime` 下载到这里）
    #
    # 早先只按 install_root 解析，开发机上装浏览器内核直接报
    # 「内置构件不存在」（实测 2026-11-21）。两个位置都试，
    # 找不到时才报错并**同时列出两处候选**，避免让人猜。
    candidates = [root / relative]
    browsers = Path(browsers_root()).resolve()
    if browsers.name == "browsers" and browsers.parent.name == ".runtime":
        # 把 `browsers/xxx` 换到 `.runtime/browsers/xxx`
        rel_parts = relative.parts
        if rel_parts and rel_parts[0] == "browsers":
            candidates.append(browsers.parent / relative)

    for candidate in candidates:
        resolved = candidate.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise RecipeError(
                f"builtin_path 逃出了安装根：{builtin_path!r}（解析为 {resolved}）"
            ) from exc
        # **文件或目录都算存在**：浏览器内核是 310 个文件的目录。
        if resolved.is_file() or resolved.is_dir():
            return resolved

    raise RecipeError(
        f"内置构件不存在：{builtin_path}（已查找："
        + "；".join(str(c) for c in candidates)
        + "）"
    )


def _hash_tree(root: Path) -> dict[str, str]:
    """对目录下所有文件算 sha256（供 `component.json` 的 files 字段）。

    排除声明文件自身（`component.json` 会被写进去，自引用会死循环）。
    """
    hashes: dict[str, str] = {}
    skip = {"component.json"}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative in skip:
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while block := handle.read(1024 * 1024):
                digest.update(block)
        hashes[relative] = f"sha256:{digest.hexdigest()}"
    return hashes


def install_from_recipe(
    recipe: dict[str, Any],
    component_root: Path,
    *,
    downloader: Any = None,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    """按 recipe 下载并装配组件（**本地组装**，字节来自上游）。

    :param downloader: 形如 ``(url, dest) -> None`` 的回调。**必须由调用方提供**——
        本模块不内置 HTTP，装配器不该成为第二个网络出口。
        生产用 `plugins.market_client.fetch_resource`（受控出站）。
    :param work_dir: 下载中转目录；默认在组件根目录下的临时目录。

    返回装配摘要（供 GUI/CLI 展示）。
    """
    validate_recipe(recipe)
    check_compatibility(recipe)

    # **只有含 remote 构件的配方才需要下载器**。
    # 全 `source: builtin` 的配方（如内置浏览器内核）字节已在安装根里，
    # 一个字节都不用拉 —— 强求下载器会把「零网络装配」堵死。
    needs_download = any(
        str(a.get("source") or "remote").lower() != "builtin"
        for a in (recipe.get("artifacts") or [])
    )
    if needs_download:
        _require(
            downloader is not None,
            "装配需要显式提供下载器（本模块不内置网络出口）",
        )

    import tempfile

    component_root = Path(component_root)
    name = str(recipe["name"])
    version = str(recipe["version"])
    # 安装目录用 **id**，不用 name —— name 是给人看的描述
    # （`内置 Chromium（随包）` 这种），含中文与括号，做目录名既难用
    # 也容易在不同文件系统上出问题。id 缺省回落到 name 保持向后兼容。
    install_id = str(recipe.get("id") or "").strip() or name
    # **避免版本号重复**：有些组件 id 本身已含版本（`webkit-2395` 且
    # version 也是 `2395`），再拼一次会得到 `webkit-2395-2395` —— 难看且
    # 让用户困惑。id 已以 `-<version>` 结尾时直接用 id。
    if install_id.endswith(f"-{version}"):
        destination = component_root / install_id
    else:
        destination = component_root / f"{install_id}-{version}"
    # 按**当前平台**筛构件：一份 recipe 可以给不同平台配不同轮子
    # （paddle 的 win_amd64 / manylinux1_x86_64 / macosx_11_0_arm64）。
    artifacts = artifacts_for_platform(recipe, current_platform())
    _require(
        bool(artifacts),
        f"recipe 没有任何适用于 {current_platform()} 的构件",
    )

    # 整个装配过程在**暂存目录**里完成，成功后原子替换到 destination。
    # 不能边下边解压到 destination：多构件时若第二个构件坏了，
    # 第一个已经落盘，留下**半装目录**——那比"没装"更难排查
    # （用户看到目录存在，以为装好了）。
    with tempfile.TemporaryDirectory(
        dir=work_dir or component_root, prefix=".recipe-"
    ) as staging:
        staging_path = Path(staging)
        downloaded = staging_path / "downloads"
        assembled = staging_path / "assembled"
        #: 目录型内置构件（浏览器内核）：index -> 真实目录。
        #: 它们不参与归档/解压，装配时只写声明。
        builtin_dirs: dict[int, Path] = {}
        downloaded.mkdir()
        assembled.mkdir()

        # 阶段一：全部下载 + 校验（任何一条失败就整体放弃）
        for index, artifact in enumerate(artifacts):
            archive = downloaded / f"artifact-{index}.zip"
            if str(artifact.get("source") or "remote").lower() == "builtin":
                # 内置构件：字节已在安装根下，**一个字节都不拉**。
                #
                # **可以是单个文件，也可以是整个目录**（浏览器内核是 310 个
                # 文件的目录）。目录形态不复制字节 —— 它本来就在安装根里，
                # 再复制一份 415 MiB 既慢又占双倍空间。直接记录来源路径，
                # 装配阶段只生成声明文件。
                builtin_source = _resolve_builtin_source(artifact["builtin_path"])
                if builtin_source.is_dir():
                    # 目录型：跳过归档/解压，标记为目录来源
                    builtin_dirs[index] = builtin_source
                    continue
                _require(
                    builtin_source.is_file(),
                    f"内置构件不存在：{artifact['builtin_path']}（解析为 {builtin_source}）",
                )
                shutil.copyfile(builtin_source, archive)
            else:
                downloader(artifact["url"], archive)
                _require(archive.is_file(), f"下载失败：{artifact['url']}")
            digest = hashlib.sha256()
            with archive.open("rb") as handle:
                while block := handle.read(1024 * 1024):
                    digest.update(block)
            actual = digest.hexdigest()
            label = (
                artifact.get("builtin_path")
                if str(artifact.get("source") or "").lower() == "builtin"
                else artifact.get("url")
            )
            _require(
                actual == artifact["sha256"],
                f"构件哈希不符（{label}）：期望 "
                f"{artifact['sha256'][:16]}…，实际 {actual[:16]}…",
            )

        # 阶段二：逐个解压到**暂存区**（不是 destination）
        for index, artifact in enumerate(artifacts):
            if index in builtin_dirs:
                # 目录型内置构件：字节已在安装根，无需解压
                continue
            archive = downloaded / f"artifact-{index}.zip"
            fmt = infer_artifact_format(artifact)
            sub = assembled / (artifact.get("extract_to") or "")
            extract_artifact(
                archive,
                sub,
                expected_sha256=artifact["sha256"],
                include=artifact.get("include"),
                artifact_format=fmt,
                filename=artifact.get("filename"),
                strip_components=int(artifact.get("strip_components") or 0),
            )

        # 阶段三：补齐运行期元数据，再确认入口存在。
        #
        # **为什么在这里生成**：`component_runner.build_wheelpack_spec()` 需要
        # `component.json` 与 `entry.json`，而 wheel 解出来只有 `site-packages/`。
        # 早先装配不生成这两个文件 ⇒ 装成功后一 run 就报「组件缺少 entry.json」
        # （真实 CI 踩到）。
        #
        # 生成的是**声明文件**（JSON），不是可执行脚本 —— 与 asset_trust 的
        # 「recipe 里不得有可执行钩子」不冲突。
        entry = recipe["entry"]
        # `entry.path` 或 `entry.path_by_platform[当前平台]` 二选一
        _entry_path_value = entry.get("path")
        if not _entry_path_value:
            _by_platform = entry.get("path_by_platform") or {}
            _entry_path_value = _by_platform.get(current_platform()) or ""
        _require(
            bool(_entry_path_value),
            f"entry 没有当前平台（{current_platform()}）的入口路径",
        )
        entry_relative = PurePosixPath(str(_entry_path_value))
        # **内置 native 组件的入口在安装根里，不在组装区**：
        # 目录型内置构件（浏览器内核）的字节本来就在包内，装配不复制它，
        # 所以入口自然不在 `assembled/` 下 —— 它的位置由
        # `builtin_dirs` 记录的目录推导。
        builtin_entry_ok = False
        if builtin_dirs and str(entry.get("runner")) == "native":
            source_dir = next(iter(builtin_dirs.values()))
            # entry.path 形如 `.runtime/browsers/chromium-1228/chrome-win64/chrome.exe`
            # 或 `browsers/chromium-1228/chrome-win64/chrome.exe`；
            # 取该目录之后的相对部分去拼真实路径。
            parts = list(entry_relative.parts)
            for anchor in ("browsers",):
                if anchor in parts:
                    idx = parts.index(anchor)
                    tail = parts[idx + 1 :]
                    # source_dir 指向 chromium-1228，tail 首段应与它同名时去掉
                    if tail and tail[0] == source_dir.name:
                        tail = tail[1:]
                    candidate = source_dir.joinpath(*tail)
                    if candidate.is_file():
                        # 入口在安装根里（内置字节不复制），确认它在就跳过检查
                        builtin_entry_ok = True
                        break
        # **目录型内置构件要生成声明文件**：字节留在安装根不复制，
        # 但 `component.json` 与 `entry.json` 是运行期必需的（否则组件
        # 装好了也启动不了）。这里为 native 形态写最小声明：
        # 入口直接指向安装根里的真实可执行文件。
        # **所有 native 组件都要生成 entry.json**：`components run` 靠它
        # 判断该 exec 还是走 worker。早先只在内置（builtin_entry_ok）分支
        # 生成，普通 native 组件（Firefox/WebKit 内核）装完缺这个文件，
        # 运行阶段只能去猜形态，报「缺少 site-packages」（真实 CI 踩到）。
        _is_native = str(entry.get("runner")) == "native"
        if builtin_entry_ok or _is_native:
            source_dir = (
                next(iter(builtin_dirs.values()))
                if builtin_dirs
                else assembled
            )
            # **入口路径的两种形态**：
            #   * 内置：指向安装根里的真实文件（绝对路径，字节不复制）
            #   * 普通 native：写**相对组件目录**的路径 —— 装配完成后
            #     staging 目录会被删除，绝对路径立刻失效（实测：写绝对路径
            #     导致 entry.json 指向已删除的临时目录）
            native_entry_path = (
                str(_resolve_builtin_source(str(_entry_path_value)))
                if builtin_entry_ok
                else PurePosixPath(str(_entry_path_value)).as_posix()
            )
            (assembled / "entry.json").write_text(
                json.dumps(
                    {
                        "name": name,
                        "version": version,
                        "engine": str(entry.get("engine") or "native"),
                        "path": native_entry_path,
                        "protocol": str(entry.get("protocol") or ""),
                        "runner": "native",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            (assembled / "component.json").write_text(
                json.dumps(
                    {
                        "name": name,
                        "version": version,
                        "engine": str(entry.get("engine") or "native"),
                        # 目录型内置构件的字节不在组件目录里，
                        # 所以 files 为空 + 显式声明 external_root。
                        # 校验由 `files.lock.json`（随配方签名）负责。
                        # **普通 native 组件要算真实文件哈希**：
                        # `verify_component_files` 拒绝空 files —— 那是
                        # 「不信任装配时校验过」的纪律，每启动前重算一遍。
                        # 内置组件的字节在组件目录外，files 只能为空，
                        # 其完整性由随配方签名的 files.lock.json 保证。
                        "files": (
                            {}
                            if builtin_entry_ok
                            else _hash_tree(assembled)
                        ),
                        **({"external_root": str(source_dir)} if builtin_entry_ok else {}),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

        if not builtin_entry_ok and not (assembled / entry_relative).is_file():
            # entry 指向的不是构件自带文件 ⇒ 由装配器生成声明（wheel-pack 形态）
            _require(
                entry.get("runner") == "worker-wheelpack",
                f"装配完成但入口不存在：{entry_relative}"
                "（只有 runner=worker-wheelpack 的组件允许自动生成入口声明）",
            )
            declared = {
                "name": name,
                "version": version,
                "engine": str(entry.get("engine") or "generic"),
                # 入口落在 site-packages 里的模块路径；由组件自身提供
                "entry": str(entry.get("module") or "omnicrawler_component.py"),
            }
            (assembled / entry_relative).write_text(
                json.dumps(declared, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            # 清单必须声明**每个文件的 sha256**：`verify_component_files()`
            # 在每次启动前按它重算一遍（不信任"装配时校验过"）。
            # 早先只写 name/version/engine、没有 files ⇒ 一 run 就报
            # 「component.json 未声明任何文件，拒绝加载」（真实 CI 踩到）。
            file_hashes: dict[str, str] = {}
            for candidate in sorted(assembled.rglob("*")):
                if not candidate.is_file():
                    continue
                relative = candidate.relative_to(assembled).as_posix()
                digest = hashlib.sha256()
                with candidate.open("rb") as handle:
                    for block in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(block)
                file_hashes[relative] = f"sha256:{digest.hexdigest()}"
            component_manifest = {
                "name": name,
                "version": version,
                "engine": str(entry.get("engine") or "generic"),
                "files": file_hashes,
            }
            (assembled / "component.json").write_text(
                json.dumps(component_manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        # 内置 native 组件的入口在安装根里（字节不复制），已在上方确认
        if not builtin_entry_ok:
            _require(
                (assembled / entry_relative).is_file(),
                f"装配完成但入口不存在：{entry_relative}",
            )
        if destination.exists():

            shutil.rmtree(destination)
        assembled.replace(destination)

    # 内置 native 组件的字节留在安装根，`destination` 里只有声明文件 ——
    # 它的入口有效性已由 `builtin_entry_ok` 分支确认，这里不再重复要求。
    # 统一取入口路径（`path` 或 `path_by_platform[当前平台]`）
    _final_entry = recipe["entry"].get("path")
    if not _final_entry:
        _final_entry = (recipe["entry"].get("path_by_platform") or {}).get(
            current_platform()
        )
    _require(bool(_final_entry), f"entry 没有当前平台（{current_platform()}）的入口路径")
    entry_path = destination / PurePosixPath(str(_final_entry))
    if not builtin_entry_ok:
        _require(entry_path.is_file(), f"装配完成但入口不存在：{entry_path}")
    elif not entry_path.is_file():
        # 内置 native：返回**安装根里的真实入口**，而不是 destination 下
        # 那个不存在的镜像路径 —— 调用方据此启动组件。
        entry_path = _resolve_builtin_source(str(_final_entry))

    return {
        "name": name,
        "version": version,
        "path": str(destination),
        "entry": str(entry_path),
        "recipe_digest": recipe_digest(recipe),
    }
