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
import re
from dataclasses import dataclass, field
from pathlib import Path

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
        results.append(audit_local_plugin(base_dir))
    else:
        for child in sorted(base_dir.iterdir()):
            if child.is_dir() and (child / "plugin.py").is_file():
                results.append(audit_local_plugin(child))
    return results
