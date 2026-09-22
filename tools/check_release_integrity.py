"""Offline release-integrity checks that do not import optional dependencies."""

from __future__ import annotations

import argparse
import ast
import base64
import configparser
import csv
import email.parser
import hashlib
import io
import itertools
import json
import re
import tarfile
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

MAX_PORTABLE_ENTRIES = 100_000
MAX_PORTABLE_EXPANDED_BYTES = 20 * 1024**3
MAX_PORTABLE_MANIFEST_BYTES = 16 * 1024**2
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}", re.IGNORECASE)
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}

# 便携包平台布局（P5：Windows zip 已有深校验，Linux tar.xz / macOS dmg 补齐）
# Linux/macOS 产物顶层是 OmniCrawler/（M4 对齐），入口可执行文件无 .exe 后缀。
# macOS 是 .app bundle，入口在 Contents/MacOS/ 下。
_PORTABLE_PLATFORM_ENTRYPOINTS: dict[str, tuple[str, ...]] = {
    "win": ("OmniCrawler.exe", "omnicrawler-cli.exe", "omnicrawler-worker.exe"),
    "linux": ("OmniCrawler", "omnicrawler", "omnicrawler-worker"),
    "mac": (
        "OmniCrawler.app/Contents/MacOS/OmniCrawler",
        "OmniCrawler.app/Contents/MacOS/omnicrawler-cli",
        "OmniCrawler.app/Contents/MacOS/omnicrawler-worker",
    ),
}
# hicolor 图标尺寸（I1b）：与 tools/check_linux_delivery.py 的同一份清单口径一致。
_HICOLOR_ICON_SIZES: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)
# macOS 额外要求 .app 目录存在（bundle 根）
_PORTABLE_PLATFORM_REQUIRED_DIRS: dict[str, tuple[str, ...]] = {
    "win": (),
    "linux": (),
    "mac": ("OmniCrawler.app", "OmniCrawler.app/Contents", "OmniCrawler.app/Contents/MacOS"),
}
_PORTABLE_REQUIRED_EXTRA: dict[str, tuple[str, ...]] = {
    "win": ("OmniCrawler-Launcher.bat", "PORTABLE_README.txt"),
    # I1：Linux 用户级安装件。这些是"装了才可能对"的东西 —— 缺一个，用户在应用
    # 菜单里要么没有条目、要么条目没有图标，而构建期是一切正常的。装配层在这里
    # 取证（缺文件即红），配合 portable_archive_smoke 的安装冒烟形成两道防线。
    "linux": (
        "installer/install-user.sh",
        "installer/uninstall-user.sh",
        "installer/omnicrawler.desktop.in",
        *(
            f"installer/icons/hicolor/{size}x{size}/apps/omnicrawler.png"
            for size in _HICOLOR_ICON_SIZES
        ),
    ),
    "mac": (),
}
# Chromium 可执行文件 glob（相对 OmniCrawler/ 根）；与 runtime_paths.py bundled_browser_executable 保持对称
_PORTABLE_CHROMIUM_PATTERNS: dict[str, tuple[str, ...]] = {
    "win": (
        "browsers/chromium-*/chrome-win/chrome.exe",
        "browsers/chromium-*/chrome-win64/chrome.exe",
    ),
    "linux": ("browsers/chromium-*/chrome-linux*/chrome",),
    "mac": (
        "browsers/chromium-*/chrome-mac/Chromium",
        "browsers/chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    ),
}

_STANDARD_FORBIDDEN_PACKAGE_ROOTS = frozenset(
    {
        "cv2",
        "duckdb",
        "opensearchpy",
        "paddle",
        "paddleocr",
        "paddlex",
        "psycopg",
        "pyarrow",
        "selenium",
    }
)


def _contains_forbidden_standard_package(path: str) -> bool:
    for part in path.casefold().split("/"):
        for package in _STANDARD_FORBIDDEN_PACKAGE_ROOTS:
            if part == package or part.startswith((f"{package}.", f"{package}-")):
                return True
    return False


@dataclass(frozen=True)
class _PortableEntry:
    """统一 zip/tar 条目模型，供跨平台 portable 校验共用。"""
    path: str  # 原始路径（含顶层 OmniCrawler/）
    size: int
    is_dir: bool
    is_symlink: bool
    encrypted: bool = False
    read: Callable[[], bytes] | None = None  # 文件内容读取（zip 随机访问，deep 校验用）
    sha256: str | None = None  # 预计算 SHA-256（tar 单遍收集，deep 校验免重读压缩流）
    content: bytes | None = None  # 小元数据文件内容缓冲（tar 单遍收集，结构校验用）


def _iter_zip_entries(archive: zipfile.ZipFile) -> Iterator[_PortableEntry]:
    for info in archive.infolist():
        _read = None

        def _read(info=info) -> bytes:
            with archive.open(info) as handle:
                return handle.read()

        yield _PortableEntry(
            path=info.filename,
            size=info.file_size,
            is_dir=info.is_dir(),
            is_symlink=_zip_entry_is_symlink(info),
            encrypted=bool(info.flag_bits & 0x1),
            read=_read if not info.is_dir() else None,
        )


# tar 深校验需单遍缓存的小元数据文件（结构校验用；按路径后缀匹配，上限按
# RUNTIME-MANIFEST 的门禁 MAX_PORTABLE_MANIFEST_BYTES 统一控制，防止超大文件撑爆内存）
_TAR_BUFFERED_SUFFIXES = ("EDITION.txt", "RUNTIME-MANIFEST.json", "omnicrawler-model-manifest.json")


def _iter_tar_entries(archive: tarfile.TarFile, *, collect_payloads: bool) -> Iterator[_PortableEntry]:
    """单遍流式收集 tar 条目（gzip 压缩流友好）。

    tar.gz 是顺序流：对 gzip 流做 extractfile() 随机访问时，每次反向定位都要
    从流头重新解压（CPython GzipFile 反向 seek = rewind + 重新解压到目标偏移），
    按 manifest 排序逐条读取退化为 O(条目数 x 包体积)。CI 实测：Standard 包
    （1930 文件）深校验耗时 59 分钟，Full 包（10805 文件、约 2GB）在 120 分钟
    job 超时内不可能跑完。

    这里只做一次前向扫描：文件内容按存储顺序读出并流式计算 SHA-256（分块，
    内存占用恒定），小元数据文件额外缓存原文供结构校验；深校验直接比对预计算
    哈希，不再触碰压缩流。
    """
    for member in archive:
        is_symlink = member.issym() or member.islnk()
        sha256: str | None = None
        content: bytes | None = None
        if member.isfile():
            buffer_content = (
                member.name.endswith(_TAR_BUFFERED_SUFFIXES)
                and member.size <= MAX_PORTABLE_MANIFEST_BYTES
            )
            digest = hashlib.sha256()
            chunks: list[bytes] = []
            extracted = archive.extractfile(member)
            if extracted is not None:
                while True:
                    chunk = extracted.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    if buffer_content:
                        chunks.append(chunk)
            if buffer_content:
                content = b"".join(chunks)
            if collect_payloads:
                sha256 = digest.hexdigest()
        yield _PortableEntry(
            path=member.name,
            size=member.size,
            is_dir=member.isdir(),
            is_symlink=is_symlink,
            read=None,
            sha256=sha256,
            content=content,
        )


def _module_file(package_root: Path, module: str) -> Path | None:
    relative = Path(*module.split("."))
    source = package_root.parent / f"{relative}.py"
    package = package_root.parent / relative / "__init__.py"
    if source.is_file():
        return source
    if package.is_file():
        return package
    return None


def _defined_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(alias.asname or alias.name.rsplit(".", 1)[-1] for alias in node.names)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names.update(target.id for target in targets if isinstance(target, ast.Name))
    return names


def _resolved_module(current: str, node: ast.ImportFrom, *, is_package: bool) -> str | None:
    if node.level == 0:
        return node.module
    package = current.split(".") if is_package else current.split(".")[:-1]
    upward = node.level - 1
    base = package[: len(package) - upward] if upward else package
    if node.module:
        base.extend(node.module.split("."))
    return ".".join(base)


def check_local_imports(source_root: Path) -> list[str]:
    package_root = source_root / "omnicrawler"
    errors: list[str] = []
    cache: dict[Path, set[str]] = {}
    for path in package_root.rglob("*.py"):
        relative = path.relative_to(source_root).with_suffix("")
        current = ".".join(relative.parts)
        is_package = path.name == "__init__.py"
        if is_package:
            current = current.rsplit(".__init__", 1)[0]
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = _resolved_module(current, node, is_package=is_package)
            if not module or not module.startswith("omnicrawler"):
                continue
            target = _module_file(package_root, module)
            if target is None:
                errors.append(f"{path.relative_to(source_root)}:{node.lineno}: missing module {module}")
                continue
            # ★ 不能用 `cache.setdefault(target, _defined_names(target))`：
            # `setdefault` 的默认参数**每次都会被求值**（Python 语义），于是同一模块
            # 被反复读盘 + `ast.parse` + `ast.walk`，缓存形同虚设。
            # 实测本文件 5.2s 里绝大部分花在这里（profiler 口径 7.6s / 总计 9.5s，
            # `ast.parse` 被调 1776 次而源文件只有约 350 个）。
            exported = cache.get(target)
            if exported is None:
                exported = _defined_names(target)
                cache[target] = exported
            for alias in node.names:
                if alias.name != "*" and alias.name not in exported:
                    submodule = _module_file(package_root, f"{module}.{alias.name}")
                    if submodule is None:
                        errors.append(f"{path.relative_to(source_root)}:{node.lineno}: {module} has no {alias.name}")
    return errors


def check_entry_points(project_root: Path, metadata: dict) -> list[str]:
    source_root = project_root / "src"
    errors: list[str] = []
    for name, target in metadata["project"].get("scripts", {}).items():
        module, _, symbol = target.partition(":")
        path = _module_file(source_root / "omnicrawler", module)
        if path is None:
            errors.append(f"entry point {name}: missing module {module}")
        elif symbol and symbol not in _defined_names(path):
            errors.append(f"entry point {name}: {module} has no {symbol}")
    return errors


def check_project(project_root: Path) -> list[str]:
    pyproject = project_root / "pyproject.toml"
    metadata = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    errors = check_entry_points(project_root, metadata)
    errors.extend(check_local_imports(project_root / "src"))
    required = ("README.md", "LICENSE", "src/omnicrawler/__init__.py")
    errors.extend(f"missing required file: {name}" for name in required if not (project_root / name).is_file())
    errors.extend(check_docker_compose_mounts(project_root))
    errors.extend(check_env_example_references(project_root))
    return sorted(set(errors))


def check_docker_compose_mounts(project_root: Path) -> list[str]:
    """docker-compose 卷挂载路径必须与镜像内 WORKDIR 对齐。

    镜像最终阶段将工作目录切到 WORKDIR（如 /data），compose 挂载若落在
    /app/... 而命令内使用相对路径（configs/project.yaml），容器内永远
    找不到文件。此检查把 Dockerfile 的最终 WORKDIR 与 compose 挂载目标
    强制绑定，防止再次错位。
    """
    dockerfile = project_root / "Dockerfile"
    compose = project_root / "docker-compose.yml"
    if not dockerfile.is_file() or not compose.is_file():
        return []
    workdirs = re.findall(r"^WORKDIR\s+(.+?)\s*$", dockerfile.read_text(encoding="utf-8"), re.MULTILINE)
    if not workdirs:
        return []
    workdir = workdirs[-1].strip()
    errors: list[str] = []
    for lineno, line in enumerate(compose.read_text(encoding="utf-8").splitlines(), 1):
        match = re.match(r"^\s*-\s*[^:]+:([^:]+?)(?::\w+)?\s*$", line)
        if not match:
            continue
        target = match.group(1).rstrip("/")
        source = line.split(":")[1].strip()
        if not (target == workdir or target.startswith(workdir + "/")):
            errors.append(
                f"docker-compose.yml:{lineno}: 挂载目标 {source} 以 {target} 开头，"
                f"但 Dockerfile 最终 WORKDIR 是 {workdir}，相对路径命令会解析失败"
            )
    return errors


def check_env_example_references(project_root: Path) -> list[str]:
    """.env.example 中每个变量必须在 src 下被引用，防止死变量误导使用者。"""
    env_example = project_root / ".env.example"
    if not env_example.is_file():
        return []
    declared = [
        line.split("=", 1)[0].strip()
        for line in env_example.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "=" in line
    ]
    if not declared:
        return []
    source_text = ""
    for source_file in (project_root / "src").rglob("*.py"):
        try:
            source_text += source_file.read_text(encoding="utf-8")
        except Exception:
            continue
    return [
        f".env.example: {name} 在 src/ 下没有任何引用（死变量，请移除或实现）"
        for name in declared
        if name not in source_text
    ]


def _wheel_modules(archive: zipfile.ZipFile) -> dict[str, tuple[str, str]]:
    modules: dict[str, tuple[str, str]] = {}
    for name in archive.namelist():
        if not name.startswith("omnicrawler/") or not name.endswith(".py"):
            continue
        module = name[:-3].replace("/", ".")
        if module.endswith(".__init__"):
            module = module[:-9]
        modules[module] = (name, archive.read(name).decode("utf-8"))
    return modules


_REQ_NAME_NORMALIZER = re.compile(r"[-_.]+")


def _normalize_requirement(name: str, specifier: str, marker: str) -> tuple[str, str, str]:
    """把一条依赖声明归一成可比较的三元组 `(包名, 版本约束, 环境标记)`。

    * **包名**按 PEP 503 归一（小写；`-` / `_` / `.` 视为同一字符）；
    * **版本约束按子句排序** —— 产物渲染成 `PyYAML<7,>=6`，锁里存 `>=6,<7`，
      顺序不同但语义相同，不排序会报假不一致；
    * **环境标记**归一引号与空白 —— 产物用 `extra == "html"`，锁用 `extra == 'html'`。
    """
    normalized_name = _REQ_NAME_NORMALIZER.sub("-", name.strip().casefold())
    clauses = sorted(part.strip() for part in specifier.split(",") if part.strip())
    # marker 归一：引号、空白，以及 `uv` 会把 `python_version` 改写成 `python_full_version`
    # （`uv.lock` 里就是这样存的）—— 不归一会在 `tomli>=2,<3; python_version < '3.11'`
    # 这类声明上误报。两者的语义边界对"本检查要判的"足够一致。
    normalized_marker = " ".join(marker.replace('"', "'").split())
    normalized_marker = normalized_marker.replace("python_full_version", "python_version")
    return normalized_name, ",".join(clauses), normalized_marker


def _wheel_requirements(wheel_path: Path) -> list[tuple[str, str, str]] | None:
    """从 wheel 的 METADATA 里取出 `Requires-Dist`（归一后）；读不到返回 None。"""
    with zipfile.ZipFile(wheel_path) as archive:
        metadata_names = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            return None
        text = archive.read(metadata_names[0]).decode("utf-8")
    message = email.parser.Parser().parsestr(text)
    entries: list[tuple[str, str, str]] = []
    for raw in message.get_all("Requires-Dist") or ():
        # 两种渲染都要认：PEP 508 `name<7,>=6; marker`（现代构建后端）
        # 与旧式 `name (<7,>=6); marker`。
        head, _, marker = str(raw).partition(";")
        # 去掉 `name[extra1,extra2]` 里的方括号段，但**保留其后的版本约束**
        # （`paddleocr[doc-parser]>=3.3,<4` —— 早先按 `split("[")` 处理会把约束一起丢掉）。
        head = re.sub(r"\[[^\]]*\]", "", head)
        match = re.match(r"\s*([A-Za-z0-9._\-]+)\s*(?:\(([^)]*)\)|([^;\s()]*))", head)
        if not match:
            continue
        specifier = match.group(2) if match.group(2) is not None else match.group(3) or ""
        entries.append(_normalize_requirement(match.group(1), specifier, marker))
    return entries


def check_wheel_requirements_against_lock(wheel_path: Path, lock_path: Path) -> list[str]:
    """wheel 的 `Requires-Dist` 必须与 `uv.lock` 里项目自身的 `requires-dist` **逐条一致**。

    这是《优化方案》§5.5「release 产物依赖与锁一致」的**严格口径**：此前只覆盖
    「可安装性」（`uv sync --locked` 成功 + 可导入），没有核对**产物自己声明了什么**。
    `[project.optional-dependencies]` 与构建后端都可能把 extras 弄丢或改名，而
    pyproject↔lock 的同步检查看不到产物这一层。

    **不需要 `packaging`**：锁里每条 `requires-dist` 自带 `specifier`，与产物归一后逐条相同，
    即已保证 `uv lock` 解析出的版本满足该约束 —— 因此不做版本可满足性推导，只做精确对账。
    """
    errors: list[str] = []
    if not lock_path.is_file():
        return [f"缺少锁文件 {lock_path.name}，无法核对产物依赖与锁一致（运行 `uv lock` 生成）"]
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    lock_packages = lock.get("package", [])
    wheel_requirements = _wheel_requirements(wheel_path)
    if wheel_requirements is None:
        return ["wheel 里找不到唯一的 METADATA，无法核对依赖声明"]

    declared: dict[tuple[str, str, str], int] = {}
    for entry in wheel_requirements:
        declared[entry] = declared.get(entry, 0) + 1

    with zipfile.ZipFile(wheel_path) as archive:
        metadata_name = next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
        metadata = email.parser.Parser().parsestr(archive.read(metadata_name).decode("utf-8"))
    project_name = _REQ_NAME_NORMALIZER.sub("-", str(metadata.get("Name", "")).casefold())
    project_version = str(metadata.get("Version", ""))

    locked_package = next(
        (
            package
            for package in lock_packages
            if _REQ_NAME_NORMALIZER.sub("-", str(package.get("name", "")).casefold()) == project_name
        ),
        None,
    )
    if locked_package is None:
        return [f"锁文件里没有 {project_name!r} 自身的条目 —— 产物与锁不是同一次构建"]
    if str(locked_package.get("version", "")) != project_version:
        errors.append(
            f"产物版本与锁不一致：wheel={project_version!r} lock={locked_package.get('version')!r}"
        )

    locked_requirements: dict[tuple[str, str, str], int] = {}
    raw_locked = locked_package.get("metadata", {}).get("requires-dist", [])
    for item in raw_locked if isinstance(raw_locked, list) else []:
        entry = _normalize_requirement(
            str(item.get("name", "")), str(item.get("specifier", "")), str(item.get("marker", ""))
        )
        locked_requirements[entry] = locked_requirements.get(entry, 0) + 1

    for entry, count in sorted(declared.items()):
        locked_count = locked_requirements.get(entry, 0)
        if locked_count < count:
            errors.append(
                "产物声明了锁里没有的依赖："
                f"{entry[0]}{entry[1]}{'; ' + entry[2] if entry[2] else ''}"
            )
    for entry, count in sorted(locked_requirements.items()):
        if declared.get(entry, 0) < count:
            errors.append(
                "锁里有的依赖产物没声明（构建时可能丢了 extras 或改名）："
                f"{entry[0]}{entry[1]}{'; ' + entry[2] if entry[2] else ''}"
            )
    return errors
def check_wheel(wheel_path: Path, *, lock_path: Path | None = None) -> list[str]:
    errors: list[str] = []
    with zipfile.ZipFile(wheel_path) as archive:
        names = set(archive.namelist())
        record_names = [name for name in names if name.endswith(".dist-info/RECORD")]
        if len(record_names) != 1:
            errors.append(f"wheel must contain exactly one RECORD, found {len(record_names)}")
        else:
            rows = csv.reader(io.StringIO(archive.read(record_names[0]).decode("utf-8")))
            for row in rows:
                # F24：RECORD 畸形行（不足三列）给出可读错误，不抛堆栈
                if len(row) < 3:
                    errors.append(f"RECORD malformed row (expected 3 columns): {row!r}")
                    continue
                name, digest, size = row[0], row[1], row[2]
                if name not in names:
                    errors.append(f"RECORD references missing file: {name}")
                    continue
                content = archive.read(name)
                if size and int(size) != len(content):
                    errors.append(f"RECORD size mismatch: {name}")
                if digest:
                    algorithm, encoded = digest.split("=", 1)
                    if algorithm != "sha256":
                        errors.append(f"unsupported RECORD digest {algorithm}: {name}")
                    else:
                        actual = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
                        if actual != encoded:
                            errors.append(f"RECORD hash mismatch: {name}")

        modules = _wheel_modules(archive)
        exported = {module: _defined_names_from_text(text, name) for module, (name, text) in modules.items()}
        for module, (name, text) in modules.items():
            tree = ast.parse(text, filename=name)
            is_package = name.endswith("/__init__.py")
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                target = _resolved_module(module, node, is_package=is_package)
                if not target or not target.startswith("omnicrawler") or target not in modules:
                    continue
                for alias in node.names:
                    if alias.name == "*" or alias.name in exported[target] or f"{target}.{alias.name}" in modules:
                        continue
                    errors.append(f"{name}:{node.lineno}: {target} has no {alias.name}")

        entry_names = [name for name in names if name.endswith(".dist-info/entry_points.txt")]
        for entry_name in entry_names:
            parser = configparser.ConfigParser()
            parser.read_string(archive.read(entry_name).decode("utf-8"))
            for command, target in parser.items("console_scripts") if parser.has_section("console_scripts") else ():
                module, _, symbol = target.partition(":")
                symbol = symbol.split("[", 1)[0]
                if module not in modules:
                    errors.append(f"entry point {command}: missing module {module}")
                elif symbol and symbol not in exported[module]:
                    errors.append(f"entry point {command}: {module} has no {symbol}")
    if lock_path is not None:
        # 严格口径：产物声明的依赖闭包必须与锁逐条一致（见函数 docstring）
        errors.extend(check_wheel_requirements_against_lock(wheel_path, lock_path))

    return sorted(set(errors))


def check_source_zip(zip_path: Path) -> list[str]:
    errors: list[str] = []
    with zipfile.ZipFile(zip_path) as archive:
        normalized: dict[str, str] = {}
        roots: set[str] = set()
        for info in archive.infolist():
            raw = info.filename
            if "\\" in raw:
                errors.append(f"archive entry uses backslashes: {raw}")
                continue
            path = PurePosixPath(raw)
            if path.is_absolute() or ".." in path.parts or not path.parts:
                errors.append(f"unsafe archive path: {raw}")
                continue
            roots.add(path.parts[0])
            key = path.as_posix().casefold()
            if key in normalized:
                errors.append(f"duplicate archive path: {normalized[key]} / {raw}")
            normalized[key] = raw
            if any(part in {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "build", "dist"} for part in path.parts):
                errors.append(f"source archive contains build/cache path: {raw}")
            if any(part.endswith(".egg-info") for part in path.parts):
                errors.append(f"source archive contains package metadata: {raw}")
            if path.suffix.casefold() in {".pyc", ".pyo"} or path.name in {".coverage", "coverage.json", "coverage.xml"}:
                errors.append(f"source archive contains generated file: {raw}")

        if len(roots) != 1:
            errors.append(f"source archive must have one root directory, found {sorted(roots)}")
            return sorted(set(errors))
        root = next(iter(roots))
        required = (
            "pyproject.toml", "README.md", "LICENSE", "src/omnicrawler/__init__.py",
            "tools/check_release_integrity.py",
        )
        names = set(archive.namelist())
        for relative in required:
            if f"{root}/{relative}" not in names:
                errors.append(f"source archive missing required file: {relative}")

        module_prefix = f"{root}/src/"
        modules: dict[str, tuple[str, str]] = {}
        for name in names:
            if not name.startswith(f"{module_prefix}omnicrawler/") or not name.endswith(".py"):
                continue
            module = name[len(module_prefix):-3].replace("/", ".")
            if module.endswith(".__init__"):
                module = module[:-9]
            modules[module] = (name, archive.read(name).decode("utf-8"))
        exported = {module: _defined_names_from_text(text, name) for module, (name, text) in modules.items()}
        for module, (name, text) in modules.items():
            tree = ast.parse(text, filename=name)
            is_package = name.endswith("/__init__.py")
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                target = _resolved_module(module, node, is_package=is_package)
                if not target or not target.startswith("omnicrawler") or target not in modules:
                    continue
                for alias in node.names:
                    if alias.name == "*" or alias.name in exported[target] or f"{target}.{alias.name}" in modules:
                        continue
                    errors.append(f"{name}:{node.lineno}: {target} has no {alias.name}")
    return sorted(set(errors))


def _portable_path_issue(raw: str) -> str | None:
    if not raw or "\x00" in raw or "\\" in raw or "//" in raw:
        return "invalid separators or empty path"
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        return "absolute or parent-relative path"
    for part in path.parts:
        if not part or part.endswith((" ", ".")) or ":" in part:
            return "path is unsafe on Windows"
        if part.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
            return "path uses a reserved Windows name"
    return None


def _portable_path_key(raw: str, *, case_sensitive: bool = False) -> str:
    # Linux 是大小写敏感文件系统，包内 OmniCrawler（GUI）与 omnicrawler（CLI）
    # 是两个合法且必须共存的入口可执行文件（_PORTABLE_PLATFORM_ENTRYPOINTS）。
    # 仅大小写不敏感平台（win/mac）才做 casefold 去重，避免误报 duplicate。
    parts = PurePosixPath(raw).parts
    if case_sensitive:
        return "/".join(part.rstrip(" .") for part in parts)
    return "/".join(part.rstrip(" .").casefold() for part in parts)


def _zip_entry_is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return mode & 0o170000 == 0o120000


def _read_small_entry_text(
    entry: _PortableEntry,
    *,
    limit: int,
    label: str,
    errors: list[str],
) -> str | None:
    if entry.size > limit:
        errors.append(f"{label} is unexpectedly large: {entry.size} bytes")
        return None
    raw: bytes | None = None
    if entry.content is not None:
        raw = entry.content
    elif entry.read is not None:
        try:
            raw = entry.read()
        except (RuntimeError, zipfile.BadZipFile, OSError) as exc:
            errors.append(f"cannot read {label}: {exc}")
            return None
    else:
        errors.append(f"cannot read {label}: unreadable entry")
        return None
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        errors.append(f"cannot read {label}: {exc}")
        return None


def _append_path_examples(errors: list[str], prefix: str, paths: set[str]) -> None:
    if not paths:
        return
    examples = sorted(paths, key=str.casefold)[:10]
    suffix = f" (and {len(paths) - len(examples)} more)" if len(paths) > len(examples) else ""
    errors.append(f"{prefix}: {', '.join(examples)}{suffix}")


def _check_portable_archive(
    entries: list[_PortableEntry],
    archive_path: Path,
    expected_edition: str | None,
    verify_payloads: bool,
    *,
    platform: str = "win",
) -> list[str]:
    errors: list[str] = []
    if not entries:
        return ["portable archive is empty"]
    if len(entries) > MAX_PORTABLE_ENTRIES:
        errors.append(f"portable archive has too many entries: {len(entries)}")
    expanded_bytes = sum(entry.size for entry in entries)
    if expanded_bytes > MAX_PORTABLE_EXPANDED_BYTES:
        errors.append(f"portable archive expands beyond the safety limit: {expanded_bytes} bytes")

    # Linux 大小写敏感：OmniCrawler（GUI 入口）与 omnicrawler（CLI 入口）合法共存；
    # win/mac 大小写不敏感才按 casefold 判重，防解压真冲突。
    _case_sensitive = platform == "linux"

    def _key(raw: str) -> str:
        return _portable_path_key(raw, case_sensitive=_case_sensitive)

    normalized: dict[str, _PortableEntry] = {}
    roots: set[str] = set()
    safe_entries: list[tuple[_PortableEntry, PurePosixPath]] = []
    for entry in entries:
        raw = entry.path
        issue = _portable_path_issue(raw)
        if issue:
            errors.append(f"unsafe portable archive path ({issue}): {raw}")
            continue
        path = PurePosixPath(raw)
        roots.add(path.parts[0])
        key = _key(raw)
        if key in normalized:
            errors.append(f"duplicate portable archive path: {normalized[key].path} / {raw}")
        else:
            normalized[key] = entry
        if entry.encrypted:
            errors.append(f"encrypted portable archive entry: {raw}")
        if entry.is_symlink:
            errors.append(f"portable archive contains symlink: {raw}")
        safe_entries.append((entry, path))

    if len(roots) != 1:
        errors.append(f"portable archive must have one root directory, found {sorted(roots)}")
        return sorted(set(errors))
    root = next(iter(roots))
    if root.casefold() != "omnicrawler":
        errors.append(f"portable archive root must be OmniCrawler, found {root}")

    relative_infos: dict[str, _PortableEntry] = {}
    relative_names: dict[str, str] = {}
    for entry, path in safe_entries:
        if len(path.parts) < 2 or path.parts[0] != root:
            continue
        relative = PurePosixPath(*path.parts[1:]).as_posix()
        key = _key(relative)
        relative_infos[key] = entry
        relative_names[key] = relative

    def file_info(relative: str) -> _PortableEntry | None:
        entry = relative_infos.get(_key(relative))
        return entry if entry is not None and not entry.is_dir else None

    entrypoints = _PORTABLE_PLATFORM_ENTRYPOINTS[platform]
    required_files = (
        *entrypoints,
        "PORTABLE.flag", "EDITION.txt", "RUNTIME-MANIFEST.json",
        "CAPABILITIES.json", "SBOM.json", "THIRD_PARTY_NOTICES.md",
        # F23：便携包门禁必须含本地说明/发布信息，否则解压后无从下手
        "RELEASE-INFO.json",
        *_PORTABLE_REQUIRED_EXTRA[platform],
    )
    for relative in required_files:
        if file_info(relative) is None:
            errors.append(f"portable archive missing required file: {relative}")
    for relative_dir in _PORTABLE_PLATFORM_REQUIRED_DIRS[platform]:
        if _key(relative_dir) not in relative_infos:
            errors.append(f"portable archive missing required directory: {relative_dir}")
    if not any(key.startswith("docs/") and not entry.is_dir for key, entry in relative_infos.items()):
        errors.append("portable archive missing required directory content: docs")
    if not any(key.startswith("_internal/") and not entry.is_dir for key, entry in relative_infos.items()):
        errors.append("portable archive missing required directory content: _internal")

    # Windows 校验 PE 头（MZ）；Linux/macOS 校验可执行位语义由 runtime-verify 兜底，
    # 这里仅确认入口文件存在且非空。
    for executable in entrypoints:
        entry = file_info(executable)
        if entry is None:
            continue
        if platform == "win":
            if entry.read is None:
                errors.append(f"cannot read portable executable {executable}")
            else:
                try:
                    if entry.read()[:2] != b"MZ":
                        errors.append(f"portable executable has no PE signature: {executable}")
                except (RuntimeError, zipfile.BadZipFile) as exc:
                    errors.append(f"cannot read portable executable {executable}: {exc}")
        elif entry.size == 0:
            errors.append(f"portable executable is empty: {executable}")
    flag = file_info("PORTABLE.flag")
    if flag is not None and flag.size != 0:
        errors.append("PORTABLE.flag must be empty")

    platform_label = {"win": "Windows", "linux": "Linux", "mac": "macOS"}[platform]
    # ★ `tar\.xz` 必须在内：Linux 产物就是 `.tar.xz`（build_linux.sh）。此前正则只认
    #   `zip|tar\.gz|dmg` ⇒ 它在 Linux 上**永不命中**，`filename_edition` 恒为 None，
    #   "文件名 edition 与声明 edition 一致"这条检查在 Linux 上**静默失效**
    #   （`EDITION.txt` 的校验仍在下方，所以不是漏洞，但是一条哑掉的判据）。
    filename_match = re.search(
        rf"{platform_label}-Portable-(Standard|Full)\.(zip|tar\.gz|tar\.xz|dmg)$",
        archive_path.name,
        re.IGNORECASE,
    )
    filename_edition = filename_match.group(1).title() if filename_match else None
    if expected_edition is not None:
        expected_edition = expected_edition.title()
        if expected_edition not in {"Standard", "Full"}:
            errors.append(f"unsupported expected portable edition: {expected_edition}")
            expected_edition = None
    if expected_edition and filename_edition and expected_edition != filename_edition:
        errors.append(
            f"portable filename edition is {filename_edition}, expected {expected_edition}"
        )

    declared_edition: str | None = None
    edition_entry = file_info("EDITION.txt")
    if edition_entry is not None:
        edition_text = _read_small_entry_text(
            edition_entry, limit=4096, label="EDITION.txt", errors=errors,
        )
        if edition_text is not None:
            match = re.fullmatch(r"OmniCrawler (Standard|Full) portable edition\s*", edition_text)
            if match:
                declared_edition = match.group(1)
            else:
                errors.append(f"invalid portable edition marker: {edition_text.strip()!r}")
    edition = expected_edition or filename_edition or declared_edition
    if edition is None:
        errors.append("portable edition could not be determined")
    for source, value in (("filename", filename_edition), ("EDITION.txt", declared_edition)):
        if edition and value and value != edition:
            errors.append(f"portable {source} declares {value}, expected {edition}")

    chromium_patterns = _PORTABLE_CHROMIUM_PATTERNS[platform]
    chromium_ok = False
    for pattern in chromium_patterns:
        compiled = re.compile(
            "^" + re.escape(pattern).replace(r"\*", "[^/]*") + "$", re.IGNORECASE,
        )
        if any(compiled.fullmatch(name) for name in relative_names.values()):
            chromium_ok = True
            break
    if not chromium_ok:
        errors.append("portable archive missing bundled Playwright Chromium")

    exe_suffix = ".exe" if platform == "win" else ""
    # macOS 是弱 Full（无稳定 paddle wheel，方案 5.3），Full 只要求 Tesseract+ChromeDriver；
    # Windows/Linux 真 Full 额外要求 Paddle 模型。
    requires_paddle = platform != "mac"
    full_only_files = (
        f"runtime/selenium/chromedriver{exe_suffix}",
        f"runtime/tesseract/tesseract{exe_suffix}",
        "runtime/tesseract/tessdata/eng.traineddata",
        "runtime/tesseract/tessdata/chi_sim.traineddata",
        "runtime/tesseract/tessdata/osd.traineddata",
    )
    if requires_paddle:
        full_only_files += ("runtime/models/paddlex/omnicrawler-model-manifest.json",)
    if edition == "Full":
        for relative in full_only_files:
            if file_info(relative) is None:
                errors.append(f"Full portable archive missing runtime asset: {relative}")
        if requires_paddle and not any(
            key.startswith("runtime/models/paddlex/official_models/")
            and key.endswith("/inference.pdiparams")
            for key in relative_infos
        ):
            errors.append("Full portable archive missing Paddle inference parameters")
        model_manifest_entry = file_info("runtime/models/paddlex/omnicrawler-model-manifest.json")
        if requires_paddle and model_manifest_entry is not None:
            model_manifest_text = _read_small_entry_text(
                model_manifest_entry,
                limit=1024 * 1024,
                label="Paddle model manifest",
                errors=errors,
            )
            model_manifest: object | None = None
            if model_manifest_text is not None:
                try:
                    model_manifest = json.loads(model_manifest_text)
                except json.JSONDecodeError as exc:
                    errors.append(f"invalid Paddle model manifest: {exc}")
            models = model_manifest.get("models") if isinstance(model_manifest, dict) else None
            # F43：兼容旧清单 verified 与新版 smoke_verified
            verified_ok = (
                isinstance(model_manifest, dict)
                and (model_manifest.get("verified") is True or model_manifest.get("smoke_verified") is True)
            )
            if not verified_ok:
                errors.append("Paddle model manifest is not marked verified")
            if not isinstance(models, list) or not models or not all(isinstance(item, str) for item in models):
                errors.append("Paddle model manifest must contain a non-empty model list")
            else:
                for model in models:
                    if not model or "/" in model or "\\" in model or model in {".", ".."}:
                        errors.append(f"invalid Paddle model name: {model!r}")
                        continue
                    parameters = (
                        f"runtime/models/paddlex/official_models/{model}/inference.pdiparams"
                    )
                    if file_info(parameters) is None:
                        errors.append(f"Paddle model is missing inference parameters: {model}")
    elif edition == "Standard":
        unexpected = {
            relative_names[key]
            for key in relative_infos
            if key in {_key(name) for name in full_only_files}
            or key.startswith("runtime/models/paddlex/")
            or _contains_forbidden_standard_package(relative_names[key])
        }
        _append_path_examples(errors, "Standard portable archive contains Full-only assets", unexpected)

    manifest_entry = file_info("RUNTIME-MANIFEST.json")
    if manifest_entry is not None:
        manifest_text = _read_small_entry_text(
            manifest_entry,
            limit=MAX_PORTABLE_MANIFEST_BYTES,
            label="RUNTIME-MANIFEST.json",
            errors=errors,
        )
        manifest: object | None = None
        if manifest_text is not None:
            try:
                manifest = json.loads(manifest_text)
            except json.JSONDecodeError as exc:
                errors.append(f"invalid RUNTIME-MANIFEST.json: {exc}")
        manifest_files = manifest.get("files") if isinstance(manifest, dict) else None
        if isinstance(manifest, dict) and manifest.get("format") != 1:
            errors.append(f"unsupported runtime manifest format: {manifest.get('format')!r}")
        if not isinstance(manifest_files, dict) or not manifest_files:
            errors.append("runtime manifest files must be a non-empty object")
        else:
            manifest_keys: set[str] = set()
            for raw_name, expected in manifest_files.items():
                if not isinstance(raw_name, str) or _portable_path_issue(raw_name):
                    errors.append(f"unsafe runtime manifest path: {raw_name!r}")
                    continue
                key = _key(raw_name)
                if key == _key("RUNTIME-MANIFEST.json"):
                    errors.append("runtime manifest must not list itself")
                    continue
                if key in manifest_keys:
                    errors.append(f"duplicate runtime manifest path: {raw_name}")
                    continue
                manifest_keys.add(key)
                if not isinstance(expected, dict):
                    errors.append(f"invalid runtime manifest record: {raw_name}")
                    continue
                expected_bytes = expected.get("bytes")
                expected_hash = expected.get("sha256")
                if (
                    not isinstance(expected_bytes, int)
                    or isinstance(expected_bytes, bool)
                    or expected_bytes < 0
                ):
                    errors.append(f"invalid runtime manifest size: {raw_name}")
                    continue
                if not isinstance(expected_hash, str) or not _SHA256_PATTERN.fullmatch(expected_hash):
                    errors.append(f"invalid runtime manifest SHA-256: {raw_name}")
                    continue
                entry = relative_infos.get(key)
                if entry is None or entry.is_dir:
                    errors.append(f"runtime manifest references missing file: {raw_name}")
                    continue
                if entry.size != expected_bytes:
                    errors.append(f"runtime manifest size mismatch: {raw_name}")
                if verify_payloads:
                    # tar 深校验在单遍收集时已预计算 SHA-256（gzip 流不可随机访问，
                    # 重读会触发 O(条目数 x 包体积) 的重复解压）；zip 走随机读取。
                    actual_hash = entry.sha256
                    if actual_hash is None and entry.read is not None:
                        digest = hashlib.sha256()
                        try:
                            digest.update(entry.read())
                        except (RuntimeError, zipfile.BadZipFile, OSError) as exc:
                            errors.append(f"cannot verify portable payload {raw_name}: {exc}")
                            continue
                        actual_hash = digest.hexdigest()
                    if actual_hash is None:
                        errors.append(f"cannot verify portable payload {raw_name}: unreadable entry")
                    elif actual_hash != expected_hash.lower():
                        errors.append(f"runtime manifest hash mismatch: {raw_name}")

            archive_keys = {
                key for key, entry in relative_infos.items()
                if not entry.is_dir and key != _key("RUNTIME-MANIFEST.json")
            }
            _append_path_examples(
                errors,
                "portable files missing from runtime manifest",
                {relative_names[key] for key in archive_keys - manifest_keys},
            )
            _append_path_examples(
                errors,
                "runtime manifest references absent portable files",
                {str(name) for name in manifest_files if _key(str(name)) in manifest_keys - archive_keys},
            )

    return sorted(set(errors))


def check_portable_zip(
    zip_path: Path,
    *,
    expected_edition: str | None = None,
    verify_payloads: bool = False,
) -> list[str]:
    try:
        with zipfile.ZipFile(zip_path) as archive:
            entries = list(_iter_zip_entries(archive))
            return _check_portable_archive(
                entries, zip_path, expected_edition, verify_payloads, platform="win",
            )
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        return [f"cannot inspect portable ZIP {zip_path}: {exc}"]


def check_portable_tar(
    tar_path: Path,
    *,
    expected_edition: str | None = None,
    verify_payloads: bool = False,
    platform: str = "linux",
) -> list[str]:
    """检查 Linux/macOS 便携包 tar.xz/tar.gz 容器（P5：与 Windows zip 深校验对齐）。"""
    try:
        with tarfile.open(tar_path, "r:*") as archive:
            entries = list(_iter_tar_entries(archive, collect_payloads=verify_payloads))
            return _check_portable_archive(
                entries, tar_path, expected_edition, verify_payloads, platform=platform,
            )
    except (OSError, RuntimeError, tarfile.TarError) as exc:
        return [f"cannot inspect portable tar {tar_path}: {exc}"]


def _defined_names_from_text(text: str, filename: str) -> set[str]:
    tree = ast.parse(text, filename=filename)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(alias.asname or alias.name.rsplit(".", 1)[-1] for alias in node.names)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names.update(target.id for target in targets if isinstance(target, ast.Name))
    return names


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_root", nargs="?", type=Path, default=Path.cwd())
    parser.add_argument("--wheel", type=Path)
    parser.add_argument(
        "--lock",
        type=Path,
        help="锁文件路径（默认 <project_root>/uv.lock）；用于核对产物依赖与锁一致",
    )
    parser.add_argument("--wheel-dir", type=Path)
    parser.add_argument("--source-zip", type=Path)
    parser.add_argument("--source-zip-dir", type=Path)
    parser.add_argument("--portable-zip", type=Path)
    parser.add_argument("--portable-zip-dir", type=Path)
    parser.add_argument("--portable-tar", type=Path)
    parser.add_argument("--portable-tar-dir", type=Path)
    parser.add_argument("--portable-platform", choices=("linux", "mac"), default="linux")
    parser.add_argument("--portable-deep", action="store_true")
    args = parser.parse_args()
    project_root = args.project_root.resolve()
    lock_path = (args.lock or (project_root / "uv.lock")).resolve()
    errors = check_project(project_root)
    if args.wheel:
        errors.extend(check_wheel(args.wheel.resolve(), lock_path=lock_path))
    if args.wheel_dir:
        wheels = sorted(args.wheel_dir.resolve().glob("*.whl"))
        if not wheels:
            errors.append(f"no wheel found in {args.wheel_dir.resolve()}")
        for wheel in wheels:
            errors.extend(
                f"{wheel.name}: {error}"
                for error in check_wheel(wheel, lock_path=lock_path)
            )
    if args.source_zip:
        errors.extend(check_source_zip(args.source_zip.resolve()))
    if args.source_zip_dir:
        archives = sorted(args.source_zip_dir.resolve().glob("*-Source.zip"))
        if not archives:
            errors.append(f"no source ZIP found in {args.source_zip_dir.resolve()}")
        for archive in archives:
            errors.extend(f"{archive.name}: {error}" for error in check_source_zip(archive))
    if args.portable_zip:
        errors.extend(check_portable_zip(args.portable_zip.resolve(), verify_payloads=args.portable_deep))
    if args.portable_zip_dir:
        archives = sorted(args.portable_zip_dir.resolve().glob("*Windows-Portable-*.zip"))
        if not archives:
            errors.append(f"no portable ZIP found in {args.portable_zip_dir.resolve()}")
        for archive in archives:
            errors.extend(
                f"{archive.name}: {error}"
                for error in check_portable_zip(archive, verify_payloads=args.portable_deep)
            )
    if args.portable_tar:
        errors.extend(
            check_portable_tar(
                args.portable_tar.resolve(),
                verify_payloads=args.portable_deep,
                platform=args.portable_platform,
            )
        )
    if args.portable_tar_dir:
        # Linux 主产物为 tar.xz（v0.9.1：tar.gz 超 GitHub Release 2GiB
        # 上限改用 xz）；macOS hdiutil 失败时回退 tar.gz，故两种都扫。
        archives = sorted(
            itertools.chain(
                args.portable_tar_dir.resolve().glob("*.tar.xz"),
                args.portable_tar_dir.resolve().glob("*.tar.gz"),
            )
        )
        if not archives:
            errors.append(f"no portable tar.xz/tar.gz found in {args.portable_tar_dir.resolve()}")
        for archive in archives:
            errors.extend(
                f"{archive.name}: {error}"
                for error in check_portable_tar(
                    archive,
                    verify_payloads=args.portable_deep,
                    platform=args.portable_platform,
                )
            )
    if errors:
        print("Release integrity check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Release integrity check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
