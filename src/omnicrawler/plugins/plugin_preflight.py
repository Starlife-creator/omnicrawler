"""插件静态预检：加载插件代码**之前**的只读检查（不执行插件代码）。

职责：源码解码、危险模块/属性/内建调用的静态扫描、元数据与权限族预检、
许可载荷指纹核验，以及供外部（含测试）直接调用的 ``_static_plugin_metadata``。
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import tokenize
from pathlib import Path
from typing import Any

from .plugin_contracts import PluginMetadata, _normalize_schema_fields

_NETWORK_MODULES = {
    "socket",
    "requests",
    "httpx",
    "aiohttp",
    "websockets",
    "urllib.request",
    "http.client",
}

# 危险模块导入：直接导入即拒绝（加载前静态检查，不执行插件代码）
_FORBIDDEN_MODULES = {"subprocess", "ctypes", "winreg", "builtins"}

# 危险属性调用：module.attr 形态（含 from module import attr）
_FORBIDDEN_ATTR_CALLS = {
    ("os", "system"),
    ("os", "startfile"),
    ("os", "remove"),
    ("os", "unlink"),
    ("os", "rmdir"),
    ("os", "removedirs"),
    ("os", "kill"),
    ("os", "popen"),
    ("os", "execl"),
    ("os", "execle"),
    ("os", "execv"),
    ("os", "execve"),
    ("os", "execvp"),
    ("os", "execvpe"),
    ("os", "spawnl"),
    ("os", "spawnle"),
    ("os", "spawnlp"),
    ("os", "spawnlpe"),
    ("os", "spawnv"),
    ("os", "spawnve"),
    ("os", "spawnvp"),
    ("os", "spawnvpe"),
    ("os", "posix_spawn"),
    ("shutil", "rmtree"),
    ("importlib", "import_module"),
    ("importlib.util", "spec_from_file_location"),
    ("importlib.util", "spec_from_loader"),
}

# 危险内建调用（eval/exec 动态执行）
_FORBIDDEN_BUILTIN_CALLS = {"eval", "exec"}
# 动态导入函数：`__import__` / `importlib.import_module` 是绕过 AST 门的
# 常规入口（`__import__('os').system('id')` 的 func.value 是 Call 而非 Name，
# 旧实现因此漏判——审查报告 B2）。任何出现都直接判危险，杜绝"借道导入"。
_FORBIDDEN_IMPORT_FUNCS = {"__import__", "import_module"}


def _decode_plugin_source(path: Path, data: bytes) -> str:
    """按 PEP 263 编码声明解码插件源码。

    尊重文件头 ``# -*- coding: xxx -*-``（tokenize.detect_encoding 负责），
    解码失败即抛 PermissionError——**绝不**返回空内容蒙混过关
    （审查报告 B2：旧实现用 utf-8 硬解，latin-1 文件抛 UnicodeDecodeError
    后被吞掉、预检返回空集 = fail-open）。
    """
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
    except (SyntaxError, LookupError) as exc:
        raise PermissionError(f"插件源码编码声明非法，拒绝加载: {path}（{exc}）") from exc
    try:
        return data.decode(encoding)
    except UnicodeDecodeError as exc:
        raise PermissionError(
            f"插件源码无法按声明的编码解码（{encoding}），拒绝加载: {path}（{exc}）"
        ) from exc


def _preflight_forbidden_patterns(path: Path, source: str, allowed: set[str]) -> tuple[set[str], set[str]]:
    """AST 静态检查插件源码，返回 (网络导入, 其他危险模式)。

    两类均为空才允许加载。``allowed`` 提供豁免的 pattern id（模块名、
    调用名如 ``os.system``），**唯一**来源：``plugins.ast_allowed_patterns``
    配置——由管理员（运行配置）控制，不由插件自己声明。

    任何解析失败都抛 PermissionError（fail-closed）；语法错误的文件本来
    也无法执行，此处显式拒绝而非静默放行。
    """
    allowed = set(allowed)
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise PermissionError(f"插件源码解析失败，拒绝加载: {path}（{exc}）") from exc

    network: set[str] = set()
    dangerous: set[str] = set()
    alias: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                top = item.name.split(".")[0]
                alias[item.asname or top] = top
                if top in _FORBIDDEN_MODULES and top not in allowed:
                    dangerous.add(top)
                if top not in allowed and any(
                    item.name == m or item.name.startswith(m + ".") for m in _NETWORK_MODULES
                ):
                    network.add(item.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            top = module.split(".")[0]
            if top in _FORBIDDEN_MODULES and top not in allowed:
                dangerous.add(top)
            if top not in allowed and any(
                module == m or module.startswith(m + ".") for m in _NETWORK_MODULES
            ):
                network.add(module)
            for item in node.names:
                if item.name == "*":
                    continue
                if top in _FORBIDDEN_MODULES and top not in allowed:
                    dangerous.add(top)
                pair = f"{top}.{item.name}"
                if (top, item.name) in _FORBIDDEN_ATTR_CALLS and pair not in allowed:
                    dangerous.add(pair)
                if item.name in _FORBIDDEN_IMPORT_FUNCS:
                    dangerous.add(f"{top}.{item.name}")
                alias[item.asname or item.name] = pair
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                if func.id in _FORBIDDEN_BUILTIN_CALLS and func.id not in allowed:
                    dangerous.add(func.id)
                if func.id == "__import__":
                    # __import__ 本身即动态导入入口：无论参数是否字面量，
                    # 一律拒绝（旧实现只在参数为常量字符串时检查网络模块，
                    # 其余情况漏过）。
                    dangerous.add("__import__")
            elif isinstance(func, ast.Attribute):
                if func.attr in _FORBIDDEN_IMPORT_FUNCS:
                    dangerous.add(f"<call>.{func.attr}")
                # 常规形态：<模块>.<属性>(...) —— 属性链逐层向上解析模块名
                resolved_module = _resolve_module_of_attribute(func, alias)
                if resolved_module is not None:
                    pair = f"{resolved_module}.{func.attr}"
                    if (resolved_module, func.attr) in _FORBIDDEN_ATTR_CALLS and pair not in allowed:
                        dangerous.add(pair)
    return network, dangerous


def _resolve_module_of_attribute(node: ast.Attribute, alias: dict[str, str]) -> str | None:
    """从属性调用链解析「模块.属性」中的模块名。

    覆盖三种形态：
    - ``os.system(...)``         → func.value 是 Name → "os"
    - ``alias.system(...)``      → func.value 是 Name，经 import as 别名映射
    - ``__import__('os').system(...)`` → func.value 是 Call —— 旧实现漏判
      的关键形态（审查报告 B2）：此处把 `__import__('<字面量>')` 的参数字面量
      当作模块名返回，命中 _FORBIDDEN_ATTR_CALLS 即拒绝。
    解析不出明确模块名时返回 None（不误报，交由其它规则兜底）。
    """
    value = node.value
    if isinstance(value, ast.Name):
        return alias.get(value.id, value.id)
    if isinstance(value, ast.Call):
        inner = value.func
        if isinstance(inner, ast.Name) and inner.id == "__import__":
            if (
                value.args
                and isinstance(value.args[0], ast.Constant)
                and isinstance(value.args[0].value, str)
            ):
                return value.args[0].value.split(".")[0]
        if (
            isinstance(inner, ast.Attribute)
            and inner.attr in _FORBIDDEN_IMPORT_FUNCS
            and value.args
            and isinstance(value.args[0], ast.Constant)
            and isinstance(value.args[0].value, str)
        ):
            return value.args[0].value.split(".")[0]
    return None


def _preflight_metadata(path: Path, source: str) -> dict[str, Any]:
    """静态读取 PLUGIN_METADATA；插件代码执行前失败关闭。"""
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise PermissionError(f"插件源码解析失败，拒绝加载: {path}（{exc}）") from exc
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == "PLUGIN_METADATA" for target in targets):
            continue
        if node.value is None:
            raise PermissionError(f"PLUGIN_METADATA 不能为空: {path}")
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, TypeError) as exc:
            raise PermissionError(f"PLUGIN_METADATA 必须是静态字面量: {path}（{exc}）") from exc
        if isinstance(value, dict):
            return value
        raise PermissionError(f"PLUGIN_METADATA 结构非法: {path}")
    return {}


def _permissions_from_metadata(path: Path, metadata: dict[str, Any]) -> set[str]:
    permissions = metadata.get("permissions", [])
    if not isinstance(permissions, (list, tuple)):
        raise PermissionError(f"PLUGIN_METADATA.permissions 必须是列表或元组: {path}")
    return {str(item).casefold() for item in permissions}


def _preflight_permissions(path: Path, source: str) -> set[str]:
    """兼容入口：静态读取插件请求权限。"""
    return _permissions_from_metadata(path, _preflight_metadata(path, source))


def _permission_artifact_sha256(path: Path, plugin_bytes: bytes) -> str:
    """权限授权绑定的稳定载荷哈希：整包优先绑定 manifest，单文件绑定源码。"""
    manifest = path.parent / "package.manifest.json"
    try:
        payload = manifest.read_bytes() if manifest.is_file() else plugin_bytes
    except OSError as exc:
        raise PermissionError(f"无法读取插件权限绑定载荷: {manifest}（{exc}）") from exc
    return hashlib.sha256(payload).hexdigest()


def _declared_creator_fingerprint(path: Path) -> str:
    """读取随载荷绑定的作者指纹；缺失时返回空串用于旧式单文件插件。"""
    for candidate, key in (
        (path.parent / "package.manifest.json", "creator_fingerprint"),
        (path.parent / "creator.identity", "key_fingerprint"),
    ):
        if not candidate.is_file():
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get(key):
            return str(data[key]).strip().casefold()
    return ""


def _resolve_plugin_permission_grant(
    *,
    plugin_id: str,
    version: str,
    artifact_sha256: str,
    creator_fingerprint: str,
    permission_grants: dict[str, Any] | None,
) -> set[str]:
    """解析插件级授权并核对版本、载荷哈希及可用的作者指纹。"""
    if permission_grants is None:
        return set()
    if not isinstance(permission_grants, dict):
        raise PermissionError("plugins.permission_grants 必须是映射")
    grant = permission_grants.get(plugin_id)
    if grant is None:
        return set()
    if not isinstance(grant, dict):
        raise PermissionError(f"插件 {plugin_id} 的 permission_grants 条目必须是映射")
    granted_hash = str(grant.get("artifact_sha256") or "").strip().casefold()
    if not granted_hash or granted_hash != artifact_sha256.casefold():
        raise PermissionError(f"插件 {plugin_id} 的授权载荷哈希不匹配，插件可能已更新")
    granted_version = str(grant.get("version") or "").strip()
    if granted_version and granted_version != version:
        raise PermissionError(
            f"插件 {plugin_id} 的授权版本为 {granted_version}，当前版本为 {version}"
        )
    granted_creator = str(grant.get("creator_fingerprint") or "").strip().casefold()
    if granted_creator and granted_creator != creator_fingerprint:
        raise PermissionError(f"插件 {plugin_id} 的授权作者指纹不匹配")
    permissions = grant.get("permissions", [])
    if not isinstance(permissions, (list, tuple)):
        raise PermissionError(f"插件 {plugin_id} 的授权 permissions 必须是列表")
    return {str(item).casefold() for item in permissions}


def _static_plugin_metadata(path: Path, source: str) -> PluginMetadata | None:
    """契约 2 subprocess 插件的静态元数据提取（不执行代码）。

    subprocess 插件不在主进程 import/exec，故无法走 ``_metadata(module)``；
    这里用与 ``_preflight_permissions`` 相同的 AST literal_eval 读 PLUGIN_METADATA
    字面量并构造 PluginMetadata（经 _normalize_schema_fields 归一）。无
    PLUGIN_METADATA 的契约 2 插件返回 None（由调用方按 legacy 名兜底）。
    fail-closed：字面量非法 → PermissionError（与权限预检同语义）。
    """
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise PermissionError(f"插件源码解析失败，拒绝加载: {path}（{exc}）") from exc
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == "PLUGIN_METADATA" for target in targets):
            continue
        if node.value is None:  # AnnAssign 无值形态（如 PLUGIN_METADATA: dict）无字面量可评估
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, TypeError) as exc:
            raise PermissionError(f"PLUGIN_METADATA 必须是静态字面量: {path}（{exc}）") from exc
        if not isinstance(value, dict):
            raise PermissionError(f"PLUGIN_METADATA 结构非法: {path}")
        legacy_name = path.parent.name if path.name == "plugin.py" else path.stem
        value.setdefault("name", legacy_name)
        result = PluginMetadata(**value)
        return _normalize_schema_fields(result, path)
    return None
