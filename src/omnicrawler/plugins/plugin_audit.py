"""本地插件自检（Phase 1：许可 + 凭据；与 CI 门 2 同逻辑，B5）。

`plugins audit --local <dir>` 的实现核心：
- 许可声明存在 + SPDX 白名单内（门 2 语义，与市场 CI generate_catalog 同源）；
- 凭据泄漏扫描（高熵 token / 密钥字段，简化版 scan_plugin 逻辑）；
- execution_mode 声明检查（Phase 1 仅提示，Phase 2 接入完整模式一致性）。

设计原则：本地绿 = CI 绿（F1 思想）——检查逻辑与市场门禁同源，
作者本地通过即 CI 许可门通过。
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 门 2 SPDX 白名单（与 OmniCrawler-market/tools/generate_catalog.py LICENSE_ALLOWLIST 同源；
# 方案 A2 单一权威来源注：变更须两侧同步 + I2 文档比对 job 校验）
LICENSE_ALLOWLIST = {
    "AGPL-3.0-only",
    "AGPL-3.0-or-later",
    "GPL-3.0-only",
    "GPL-3.0-or-later",
    "MIT",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "CC0-1.0",
    "Unlicense",
}

# 凭据扫描：与市场仓 scan_plugin.py 同族的正则（简化：本地自检面向作者自查，
# 高误报容忍度低于 CI 门禁——命中给警告而非硬失败）
_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("openai_key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("private_key_block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----")),
    ("bearer_token", re.compile(r"(?i)['\"]?authorization['\"]?\s*[:=]\s*['\"]bearer\s+[A-Za-z0-9._\-]{20,}")),
]
# 豁免 secret:// 引用（主仓凭据引用语法，值是密钥库条目名非明文）
_SECRET_REF_RE = re.compile(r"secret://[A-Za-z0-9_\-]+")


@dataclass
class AuditFinding:
    """单条审计发现。"""

    level: str  # error | warning | info
    code: str
    message: str


@dataclass
class AuditResult:
    """单个插件目录的审计结果。"""

    plugin_dir: str
    findings: list[AuditFinding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(f.level == "error" for f in self.findings)

    def to_dict(self) -> dict:
        return {
            "plugin_dir": self.plugin_dir,
            "ok": self.ok,
            "findings": [
                {"level": f.level, "code": f.code, "message": f.message}
                for f in self.findings
            ],
        }


def _read_metadata_license(plugin_dir: Path) -> str | None:
    """从 PLUGIN_METADATA（AST 静态读取）或 plugin.yaml 获取 license 声明。"""
    plugin_file = plugin_dir / "plugin.py"
    if plugin_file.is_file():
        try:
            tree = ast.parse(plugin_file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == "PLUGIN_METADATA":
                            if isinstance(node.value, (ast.Dict, ast.Constant)):
                                try:
                                    metadata = ast.literal_eval(node.value)
                                    if isinstance(metadata, dict):
                                        return str(metadata.get("license") or "") or None
                                except (ValueError, TypeError):
                                    pass
        except SyntaxError:
            pass
    manifest = plugin_dir / "plugin.yaml"
    if manifest.is_file():
        try:
            import yaml

            data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return str(data.get("license") or "") or None
        except Exception:  # noqa: BLE001 - 解析失败视为无声明
            pass
    return None


def _scan_credentials(plugin_dir: Path) -> list[AuditFinding]:
    """凭据泄漏扫描（文本文件，跳过二进制与签名文件）。"""
    findings: list[AuditFinding] = []
    skip_suffixes = {".sig", ".identity", ".pem", ".png", ".jpg", ".pdf"}
    for path in sorted(plugin_dir.rglob("*")):
        if not path.is_file() or path.suffix in skip_suffixes:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        # secret:// 引用豁免：先剔除再扫
        scrubbed = _SECRET_REF_RE.sub("secret://<ref>", text)
        for name, pattern in _SECRET_PATTERNS:
            if pattern.search(scrubbed):
                findings.append(
                    AuditFinding(
                        level="warning",
                        code="credential_scan",
                        message=f"{path.name}: 疑似 {name} 凭据泄漏（请改用 secret:// 引用或环境变量注入）",
                    )
                )
    return findings


def audit_local_plugin(plugin_dir: Path) -> AuditResult:
    """审计单个本地插件目录（Phase 1：许可 + 凭据）。"""
    plugin_dir = plugin_dir.resolve()
    result = AuditResult(plugin_dir=str(plugin_dir))

    if not plugin_dir.is_dir():
        result.findings.append(
            AuditFinding(level="error", code="dir_missing", message=f"目录不存在: {plugin_dir}")
        )
        return result

    # 门 2：许可声明检查（与 generate_catalog._entry_from_yaml 同源逻辑）
    license_id = _read_metadata_license(plugin_dir)
    if not license_id:
        result.findings.append(
            AuditFinding(
                level="error",
                code="license_missing",
                message="license 未声明（Phase 1 起必填，无隐式默认；插件请从 SPDX 白名单选择）",
            )
        )
    elif license_id not in LICENSE_ALLOWLIST:
        result.findings.append(
            AuditFinding(
                level="error",
                code="license_not_allowlisted",
                message=f"许可 {license_id!r} 不在 SPDX 白名单内（门 2）：{sorted(LICENSE_ALLOWLIST)}",
            )
        )
    else:
        result.findings.append(
            AuditFinding(level="info", code="license_ok", message=f"许可声明合法: {license_id}")
        )

    # 凭据扫描
    result.findings.extend(_scan_credentials(plugin_dir))

    # Phase 2a（B5）：契约形态与 execution_mode 一致性检查
    result.findings.extend(_check_contract_consistency(plugin_dir))

    return result


def _check_contract_consistency(plugin_dir: Path) -> list[AuditFinding]:
    """契约形态 ↔ execution_mode 一致性（方案第 17 轮：契约 1 不能 subprocess）。

    - 契约 1（仅 register）声明 execution_mode=subprocess → error（无宿主注册面，
      无法子进程运行；须迁移契约 2 或改 in_process）
    - 契约形态未知（无 handle 也无 register）→ warning
    """
    from .plugin_router import detect_contract_shape

    findings: list[AuditFinding] = []
    plugin_file = plugin_dir / "plugin.py"
    if not plugin_file.is_file():
        return findings
    try:
        source = plugin_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return findings
    shape = detect_contract_shape(source)

    execution_mode = ""
    try:
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "PLUGIN_METADATA":
                        meta = ast.literal_eval(node.value)
                        if isinstance(meta, dict):
                            execution_mode = str(meta.get("execution_mode") or "").strip()
    except (SyntaxError, ValueError, TypeError):
        pass

    if shape == 0:
        findings.append(
            AuditFinding(
                level="warning",
                code="contract_unknown",
                message="未检测到 handle（契约2）或 register（契约1）入口，契约形态未知",
            )
        )
    if shape == 1 and (execution_mode == "" or execution_mode == "subprocess"):
        # 契约 1 + subprocess（含缺省）：0.10 语义下拒载，本地自检给 error
        findings.append(
            AuditFinding(
                level="error",
                code="contract1_cannot_subprocess",
                message="契约 1（register）插件不能以 subprocess 运行"
                "（请迁移契约 2 或显式声明 execution_mode: in_process）",
            )
        )
    if shape == 2:
        findings.append(
            AuditFinding(
                level="info", code="contract2", message="契约 2（handle）：支持 subprocess 隔离运行"
            )
        )
    return findings


def probe_sandbox_backend() -> dict:
    """沙箱可用性探测（B5：plugins audit 沙箱探测项；E_UNSUPPORTED_ENV 前置）。

    返回 {backend, ok, detail}：
    - 冻结模式：检查 omnicrawler-sandbox-host.exe 存在性
    - 源码模式：实际 spawn 子进程跑一次 system.info 往返（最真实的可用性验证）
    """
    from . import plugin_backend

    backend = plugin_backend.backend_name()
    try:
        command, _ = plugin_backend.resolve_backend_command()
    except FileNotFoundError as exc:
        return {"backend": backend, "ok": False, "detail": str(exc)}

    # 源码模式实测：spawn + 一次最小往返（handle echo）
    import json
    import subprocess
    import tempfile

    probe_dir = Path(tempfile.mkdtemp(prefix="omnicrawler-probe-"))
    probe_file = probe_dir / "probe.py"
    probe_file.write_text("def handle(op, payload):\n    return {'ok': True}\n", encoding="utf-8")
    full_command = [*command, "probe", str(probe_dir)]
    try:
        completed = subprocess.run(
            full_command,
            input=json.dumps({"v": 1, "operation": "ping", "payload": {}, "request_id": "probe"}),
            capture_output=True, text=True, encoding="utf-8", timeout=20, check=False,
        )
        ok = completed.returncode == 0 and '"ok": true' in completed.stdout
        detail = completed.stderr[-200:] if not ok else "subprocess 往返正常"
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, f"探测失败: {exc}"
    finally:
        import shutil

        shutil.rmtree(probe_dir, ignore_errors=True)
    return {"backend": backend, "ok": ok, "detail": detail}


def audit_local_directory(base_dir: Path) -> list[AuditResult]:
    """审计目录下全部插件（每个子目录含 plugin.py 视为一个插件）。"""
    results: list[AuditResult] = []
    base_dir = base_dir.resolve()
    if not base_dir.is_dir():
        return results
    # 目录本身是插件（含 plugin.py）→ 审计自身；否则遍历子目录
    if (base_dir / "plugin.py").is_file() or (base_dir / "plugin.yaml").is_file():
        results.append(audit_local_plugin_full(base_dir))
    else:
        for child in sorted(base_dir.iterdir()):
            if child.is_dir() and (child / "plugin.py").is_file():
                results.append(audit_local_plugin_full(child))
    return results


# ============================================================================
# Phase 2a 门 1 / 门 3（方案第 26/67 轮；与 CI generate_catalog 同源逻辑）
# ============================================================================

# 门 3 依赖许可白名单（A2 单一权威来源：与 generate_catalog.py LICENSE_ALLOWLIST
# 同源；变更须两侧同步 + I2 文档比对 job 校验）
_DEPENDENCY_LICENSE_ALLOWLIST = {
    "AGPL-3.0-only", "AGPL-3.0-or-later", "GPL-3.0-only", "GPL-3.0-or-later",
    "MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "CC0-1.0", "Unlicense",
}

# 门 1：subprocess 插件禁 import 的宿主核心模块前缀（隔离边界）
_HOST_CORE_PREFIXES = ("omnicrawler.", "omnicrawler")
# 门 1：subprocess 禁声明的权限族（原生 ui 必须 in_process；hook 是扩展类型而非权限）
_SUBPROCESS_FORBIDDEN_PERMISSION_PREFIXES = ("ui:", "hook")


def _extract_imports(source: str) -> set[str]:
    """AST 提取顶层模块名集合（import x / from x.y import z → 'x'）。"""
    import ast

    names: set[str] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return names
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
    return names


def _extract_static_metadata(plugin_dir: Path) -> dict | None:
    """静态读 PLUGIN_METADATA 字面量（不执行代码）。"""
    import ast

    plugin_file = plugin_dir / "plugin.py"
    if not plugin_file.is_file():
        return None
    try:
        tree = ast.parse(plugin_file.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "PLUGIN_METADATA":
                    try:
                        value = ast.literal_eval(node.value)
                        if isinstance(value, dict):
                            return value
                    except (ValueError, TypeError):
                        return None
    return None


def gate_declaration_consistency(plugin_dir: Path) -> list[AuditFinding]:
    """门 1：execution_mode 与代码/权限声明一致性（方案第 26/50 轮）。

    - subprocess 声明却 import omnicrawler 核心 → error（隔离边界破坏）
    - subprocess 声明 ui:* / 旧式 hook 权限 → error（hook 应写入 plugin_types）
    - network 权限无 domains 声明 → error
    - files:read 权限无 input_files 白名单 → error（第 50 轮）
    """
    findings: list[AuditFinding] = []
    meta = _extract_static_metadata(plugin_dir)
    plugin_file = plugin_dir / "plugin.py"
    if meta is None or not plugin_file.is_file():
        return findings  # 无静态元数据（契约 1）不在此门裁决

    execution_mode = str(meta.get("execution_mode", "subprocess")).strip() or "subprocess"
    permissions = {str(p).casefold() for p in meta.get("permissions", [])}
    from .plugins import OFFICIAL_PLUGIN_TYPES, SUBPROCESS_ADAPTER_PLUGIN_TYPES

    raw_plugin_types = meta.get("plugin_types", [])
    if not isinstance(raw_plugin_types, (list, tuple)):
        findings.append(
            AuditFinding(
                level="error",
                code="gate1_plugin_types_not_list",
                message="plugin_types 必须是运行扩展点列表",
            )
        )
        plugin_types: set[str] = set()
    else:
        plugin_types = {str(item).strip().casefold() for item in raw_plugin_types if str(item).strip()}
    unknown_types = plugin_types - OFFICIAL_PLUGIN_TYPES
    if unknown_types:
        findings.append(
            AuditFinding(
                level="error",
                code="gate1_unknown_plugin_type",
                message=(
                    f"未知运行扩展点: {sorted(unknown_types)}；"
                    "自定义业务分类请使用 category/tags"
                ),
            )
        )
    unsupported_types = plugin_types - SUBPROCESS_ADAPTER_PLUGIN_TYPES
    if execution_mode == "subprocess" and unsupported_types and not unknown_types:
        findings.append(
            AuditFinding(
                level="warning",
                code="gate1_plugin_type_not_wired",
                message=f"当前版本尚未接入这些契约 2 扩展点: {sorted(unsupported_types)}",
            )
        )
    source = plugin_file.read_text(encoding="utf-8")

    if execution_mode == "subprocess":
        imports = _extract_imports(source)
        host_imports = {
            name for name in imports
            if name == "omnicrawler" or name.startswith(_HOST_CORE_PREFIXES)
        }
        if host_imports:
            findings.append(
                AuditFinding(
                    level="error",
                    code="gate1_subprocess_imports_host",
                    message=(
                        f"subprocess 插件禁止 import 宿主核心: {sorted(host_imports)}"
                        "（隔离边界；请改用能力代理 omnicrawler_sdk）"
                    ),
                )
            )
        forbidden_perms = {
            p for p in permissions
            if any(p.startswith(prefix) for prefix in _SUBPROCESS_FORBIDDEN_PERMISSION_PREFIXES)
        }
        if forbidden_perms:
            findings.append(
                AuditFinding(
                    level="error",
                    code="gate1_subprocess_forbidden_permission",
                    message=(
                        f"subprocess 插件不得声明 ui:*/旧式 hook 权限: {sorted(forbidden_perms)}"
                        "（hook 应声明为 plugin_types 扩展点；ui 需 in_process）"
                    ),
                )
            )

    if "network" in permissions or "network:scoped" in permissions:
        domains = meta.get("domains", [])
        if not domains:
            findings.append(
                AuditFinding(
                    level="error",
                    code="gate1_network_without_domains",
                    message="network 权限必须声明 domains（egress 边界）",
                )
            )

    if "files:read" in permissions:
        input_files = meta.get("input_files", [])
        if not input_files:
            findings.append(
                AuditFinding(
                    level="error",
                    code="gate1_files_read_without_allowlist",
                    message="files:read 权限必须声明 input_files 路径白名单（第 50 轮）",
                )
            )

    return findings


def gate_dependencies_consistency(plugin_dir: Path) -> list[AuditFinding]:
    """门 3：dependencies 双向一致性（第 67 轮）。

    - 声明的每个依赖（name/version/license）必须在实测导入图中存在
    - 实测导入中出现但**未声明**的第三方依赖 → 拒
    - dependencies 子字段 license 须在白名单内（A2）
    空 dependencies（[]）合法——零第三方依赖插件。
    """
    findings: list[AuditFinding] = []
    meta = _extract_static_metadata(plugin_dir)
    plugin_file = plugin_dir / "plugin.py"
    if meta is None or not plugin_file.is_file():
        return findings

    declared = meta.get("dependencies")
    if declared is None:
        # 第 67 轮：dependencies 必填，缺省视为非法（除非零依赖显式 []）
        findings.append(
            AuditFinding(
                level="warning",
                code="gate3_dependencies_missing",
                message="PLUGIN_METADATA.dependencies 未声明（零依赖请显式填 []）",
            )
        )
        declared = []
    if not isinstance(declared, list):
        findings.append(
            AuditFinding(
                level="error", code="gate3_dependencies_not_list",
                message="dependencies 必须是列表 [{name, version, license}]",
            )
        )
        return findings

    declared_names: dict[str, dict] = {}
    for dep in declared:
        if not isinstance(dep, dict):
            findings.append(
                AuditFinding(
                    level="error", code="gate3_dependency_not_dict",
                    message=f"dependencies 条目非法: {dep!r}",
                )
            )
            continue
        name = str(dep.get("name", "")).strip()
        if not name:
            findings.append(
                AuditFinding(level="error", code="gate3_dependency_no_name", message="依赖缺 name")
            )
            continue
        declared_names[name] = dep
        license_id = str(dep.get("license", "")).strip()
        if not license_id:
            findings.append(
                AuditFinding(
                    level="error", code="gate3_dependency_no_license",
                    message=f"依赖 {name} 缺 license（门 2 白名单校验前提）",
                )
            )
        elif license_id not in _DEPENDENCY_LICENSE_ALLOWLIST:
            findings.append(
                AuditFinding(
                    level="error", code="gate3_dependency_license_not_allowlisted",
                    message=f"依赖 {name} 许可 {license_id!r} 不在白名单",
                )
            )

    # 实测导入图（AST）—— 与声明严格互证
    import sys as _sys

    source = plugin_file.read_text(encoding="utf-8")
    actual_imports = _extract_imports(source)
    stdlib = set(getattr(_sys, "stdlib_module_names", set()))
    # 第三方 = 非标准库、非插件自身、非宿主（宿主由门 1 裁决）
    third_party = {
        name for name in actual_imports
        if name not in stdlib
        and name != "omnicrawler"
        and name != plugin_file.stem
        and not name.startswith("omnicrawler_sdk")
    }

    for name in sorted(declared_names):
        if name not in third_party:
            findings.append(
                AuditFinding(
                    level="error", code="gate3_declared_but_not_imported",
                    message=f"声明的依赖 {name} 未在实测导入图中出现（声明与实现互证失败）",
                )
            )
    for name in sorted(third_party):
        if name not in declared_names:
            findings.append(
                AuditFinding(
                    level="error", code="gate3_imported_but_not_declared",
                    message=f"实测导入 {name} 未在 dependencies 声明（未声明即拒，第 67 轮）",
                )
            )

    return findings


def audit_local_plugin_full(plugin_dir: Path) -> AuditResult:
    """完整审计：Phase 1（许可+凭据+契约一致性）+ Phase 2a 门 1/门 3。"""
    result = audit_local_plugin(plugin_dir)  # 已含契约一致性检查
    result.findings.extend(gate_declaration_consistency(plugin_dir))
    result.findings.extend(gate_dependencies_consistency(plugin_dir))
    return result


# ============================================================================
# Phase 2a H4：环境诊断报告（plugins audit --report；第 68/69/71 轮）
# ============================================================================

# 报告 schema 版本（第 71 轮）：随字段集变更单调递增（H7 语义）。
REPORT_SCHEMA_VERSION = 1

# 报告字段白名单（第 69 轮：防隐私意外）——仅从固定字段清单生成，
# 任何不在清单内的运行时数据（路径/插件名/记录内容/审计 payload/凭据引用）
# 一律不进报告。改字段清单 = 改代码 + 测试同步（H7 规则变更语义）。
REPORT_FIELD_WHITELIST = frozenset({
    "report_schema",
    "os",
    "os_version",
    "kernel",
    "python_version",
    "app_version",
    "sandbox_backend",
    "sandbox_available",
    "sandbox_detail",
    "sandbox_supported_range",
    "host_exe_present",
})


def generate_environment_report() -> str:
    """生成脱敏环境诊断报告（Markdown，零插件明细/零路径/零用户标识）。

    H4 回传通道：用户遇 E_UNSUPPORTED_ENV 拒载/沙箱故障时自愿粘贴至
    GitHub Issue。字段白名单 + 输出前自检断言（越界即报错不生成）。
    """
    import platform

    from .. import __version__
    from . import plugin_backend, plugin_os_sandbox

    probe = plugin_os_sandbox.probe_os_sandbox()
    # 冻结产物才有伴生宿主 exe；源码模式恒为 False
    host_present = plugin_backend.is_frozen() and plugin_backend.bundled_sandbox_host() is not None

    report: dict[str, object] = {
        "report_schema": REPORT_SCHEMA_VERSION,
        "os": platform.system(),
        "os_version": platform.version(),
        "kernel": platform.release(),
        "python_version": platform.python_version(),
        "app_version": __version__,
        "sandbox_backend": probe.backend,
        "sandbox_available": probe.available,
        "sandbox_detail": probe.detail,
        "sandbox_supported_range": probe.supported_range,
        "host_exe_present": host_present,
    }

    # 自检断言：字段越界即报错不生成（第 69 轮防隐私意外）
    out_of_whitelist = set(report) - REPORT_FIELD_WHITELIST
    if out_of_whitelist:
        raise ValueError(f"报告字段越界白名单（拒绝生成）: {sorted(out_of_whitelist)}")

    lines = [
        f"```report_schema: {REPORT_SCHEMA_VERSION}",
        "| 字段 | 值 |",
        "| --- | --- |",
    ]
    for key in sorted(report):
        lines.append(f"| {key} | {report[key]} |")
    lines.append("```")
    lines.append("")
    lines.append("<!-- 将以上内容粘贴至 GitHub Issue；本报告零插件明细/零路径/零用户标识 -->")
    return "\n".join(lines)


# ============================================================================
# Phase 2b H4：共现事件 SIEM 导出（plugins audit --export-egress；第 66/70 轮）
# ============================================================================

# 共现事件导出字段（第 70 轮校准为企业化预留接口面，文档化落地）：
# UTC 时间戳/plugin_id/version/路径/域名/判定/会话号——对齐 C6 审计 schema。
_EGRESS_EXPORT_FIELDS = (
    "timestamp_utc",
    "plugin_id",
    "plugin_version",
    "operation",
    "domain",
    "decision",
    "session_id",
)


def export_egress_cooccurrence(state_store: Any, output_path: Path) -> int:
    """把审计库中的共现事件导出为 JSONL（SIEM 关联分析）。

    broker 侧共现事件经 audit_hook 写入审计（action=plugin.egress_cooccurrence，
    details 含 plugin_id/decision/records_read_before）。此处从审计库检索并
    以固定字段清单输出（零插件明细外泄——只导出共现判定元数据）。

    返回导出行数；无共现事件 → 空文件 + 0。
    """
    rows = state_store.rows(
        "SELECT run_id, action, actor, details_json, created_at "
        "FROM audit_events WHERE action=? ORDER BY created_at",
        ("plugin.egress_cooccurrence",),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            try:
                details = json.loads(row["details_json"])
            except (json.JSONDecodeError, TypeError):
                details = {}
            entry = {
                "timestamp_utc": str(row.get("created_at", "")),
                "plugin_id": str(details.get("plugin_id", "")),
                "plugin_version": str(details.get("plugin_version", "")),
                "operation": "records.read->network.fetch",
                "domain": str(details.get("domain", "")),
                "decision": str(details.get("decision", "cooccurrence_risk")),
                "session_id": str(details.get("session_id", row.get("run_id", ""))),
            }
            # 字段白名单导出：只写清单字段（防隐私意外，H7 语义）
            handle.write(json.dumps({k: entry[k] for k in _EGRESS_EXPORT_FIELDS}, ensure_ascii=False) + "\n")
            count += 1
    return count
