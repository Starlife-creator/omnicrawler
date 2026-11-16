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
import sys
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any

from ..core.archive_security import (
    UnsafePackageError,
    copy_zip_member,
)

#: 支持的 recipe schema 版本。未知版本一律拒绝（不猜测、不降级）。
SUPPORTED_SCHEMA = 1

#: recipe 文件只接受这些扩展名。**刻意不含** .sh/.bat/.py/.exe/.ps1：
#: recipe 必须是数据，不是程序。
ALLOWED_RECIPE_SUFFIXES = (".json",)

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
        "extract_to",
        "include",
        "strip_components",
    }
)


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
        schema == SUPPORTED_SCHEMA,
        f"不支持的 recipe schema 版本：{schema!r}（支持 {SUPPORTED_SCHEMA}）",
    )

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

    entry = recipe.get("entry")
    _require(isinstance(entry, dict), "recipe 需要 entry（入口声明）")
    assert isinstance(entry, dict)  # 供类型检查收窄
    _require(
        set(entry) <= {"path", "protocol", "engine"},
        f"entry 含不允许的键：{sorted(set(entry) - {'path', 'protocol', 'engine'})}",
    )
    path = entry.get("path")
    _require(bool(isinstance(path, str) and path.strip()), "entry.path 不能为空")
    assert isinstance(path, str)
    _safe_relative(path)

    return recipe


def _validate_artifact(artifact: Any, index: int) -> None:
    _require(isinstance(artifact, dict), f"artifact #{index} 必须是对象")

    unknown = set(artifact) - ALLOWED_ARTIFACT_KEYS
    _require(not unknown, f"artifact #{index} 含不允许的键：{sorted(unknown)}")

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

    extract_to = artifact.get("extract_to", "")
    _require(isinstance(extract_to, str), f"artifact #{index} 的 extract_to 必须是字符串")
    if extract_to:
        _safe_relative(extract_to)

    size = artifact.get("size")
    if size is not None:
        _require(
            type(size) is int and size >= 0,
            f"artifact #{index} 的 size 必须是非负整数",
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
    """拒绝绝对路径与 `..`（路径穿越）。"""
    path = PurePosixPath(value.replace("\\", "/"))
    _require(not path.is_absolute(), f"不允许绝对路径：{value!r}")
    _require(".." not in path.parts, f"不允许路径穿越（..）：{value!r}")
    _require(bool(path.parts), f"路径不能为空：{value!r}")
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


def current_architecture() -> str:
    """架构名（与 recipe 的 architectures 对齐）。"""
    import platform

    machine = platform.machine().lower()
    return {
        "amd64": "x86_64",
        "x86_64": "x86_64",
        "arm64": "aarch64",
        "aarch64": "aarch64",
    }.get(machine, machine)


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
    _require(
        target_platform in platforms,
        f"此组件不支持当前平台：需要 {platforms}，当前 {target_platform}",
    )

    architectures = recipe.get("architectures") or []
    _require(
        target_arch in architectures,
        f"此组件不支持当前架构：需要 {architectures}，当前 {target_arch}",
    )

    required_abi = recipe.get("python_abi")
    if required_abi:
        _require(
            str(required_abi) == target_abi,
            f"组件要求的 Python ABI 与当前不符：需要 {required_abi}，当前 {target_abi}。"
            "跨 ABI 的二进制扩展无法加载，装上也用不了。",
        )


def extract_artifact(
    archive: Path,
    target: Path,
    *,
    expected_sha256: str | None = None,
    include: Iterable[str] | None = None,
) -> None:
    """把构件解压到 ``target``，**先校验后落盘**。

    :param expected_sha256: 期望的归档哈希。为 None 时**不做哈希校验**
        （只应在测试或调用方已自行校验时使用）。
    :param include: 只解压这些相对路径；None 表示全部。

    **两阶段**：先在临时目录完成校验与解压，成功后才原子替换到 ``target``。
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

    wanted = None
    if include is not None:
        wanted = {str(_safe_relative(item)) for item in include}

    try:
        with zipfile.ZipFile(archive) as zf:
            resolved = _validate_and_select(zf, wanted)
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=target.parent, prefix=".assemble-") as staging:
                staging_path = Path(staging)
                # 用 copy_zip_member 而不是 read_zip_member：后者把整个成员读进内存，
                # 而组件构件可能是几百 MB。前者流式写盘 + 原子改名 + 带出权限位。
                for info, relative in resolved:
                    destination = staging_path / relative
                    copy_zip_member(zf, info, destination)
                # 全部成功后才原子替换
                if target.exists():
                    import shutil

                    shutil.rmtree(target)
                staging_path.replace(target)
    except UnsafePackageError as exc:
        raise RecipeError(f"构件包不安全，已拒绝装配：{exc}") from exc


def _validate_and_select(zf: zipfile.ZipFile, wanted: set[str] | None) -> list[tuple[Any, PurePosixPath]]:
    """逐成员做路径安全检查，并按 include 过滤。"""
    selected: list[tuple[Any, PurePosixPath]] = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        relative = _safe_relative(info.filename)
        if wanted is not None and str(relative) not in wanted:
            continue
        selected.append((info, relative))
    return selected


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

    _require(downloader is not None, "装配需要显式提供下载器（本模块不内置网络出口）")

    import tempfile

    component_root = Path(component_root)
    name = str(recipe["name"])
    version = str(recipe["version"])
    destination = component_root / f"{name}-{version}"
    artifacts = recipe["artifacts"]

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
        downloaded.mkdir()
        assembled.mkdir()

        # 阶段一：全部下载 + 校验（任何一条失败就整体放弃）
        for index, artifact in enumerate(artifacts):
            archive = downloaded / f"artifact-{index}.zip"
            downloader(artifact["url"], archive)
            _require(archive.is_file(), f"下载失败：{artifact['url']}")
            digest = hashlib.sha256()
            with archive.open("rb") as handle:
                while block := handle.read(1024 * 1024):
                    digest.update(block)
            actual = digest.hexdigest()
            _require(
                actual == artifact["sha256"],
                f"构件哈希不符（{artifact['url']}）：期望 "
                f"{artifact['sha256'][:16]}…，实际 {actual[:16]}…",
            )

        # 阶段二：逐个解压到**暂存区**（不是 destination）
        for index, artifact in enumerate(artifacts):
            archive = downloaded / f"artifact-{index}.zip"
            sub = assembled / (artifact.get("extract_to") or "")
            extract_artifact(
                archive,
                sub,
                expected_sha256=artifact["sha256"],
                include=artifact.get("include"),
            )

        # 阶段三：入口必须存在，然后才原子替换
        entry = recipe["entry"]
        entry_relative = PurePosixPath(entry["path"])
        _require(
            (assembled / entry_relative).is_file(),
            f"装配完成但入口不存在：{entry_relative}",
        )
        if destination.exists():
            import shutil

            shutil.rmtree(destination)
        assembled.replace(destination)

    entry_path = destination / PurePosixPath(recipe["entry"]["path"])
    _require(entry_path.is_file(), f"装配完成但入口不存在：{entry_path}")

    return {
        "name": name,
        "version": version,
        "path": str(destination),
        "entry": str(entry_path),
        "recipe_digest": recipe_digest(recipe),
    }
