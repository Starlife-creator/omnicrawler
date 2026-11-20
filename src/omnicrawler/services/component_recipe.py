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
SUPPORTED_SCHEMA = 1

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
    }
)

#: entry 允许的键。
#: entry 允许的键。
#:
#: `module`：入口在 `site-packages` 内的相对路径。**只对 `runner=worker-wheelpack`
#: 有意义** —— 此时 `entry.path` 指向装配器生成的声明文件，真正的入口是 `module`。
#: 声明的是**组件自身依赖**里的模块，不是本仓提供的脚本（与 asset_trust 的
#: 「recipe 不得含可执行钩子」不冲突：那是"不能带脚本进来"，这是"指个已有模块"）。
ALLOWED_ENTRY_KEYS = frozenset({"path", "protocol", "engine", "runner", "module"})


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
            import shutil

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
        entry_relative = PurePosixPath(entry["path"])
        if not (assembled / entry_relative).is_file():
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
