"""D4 沙箱内纵深防御（Phase 2a；OS 层之外的第二道防线）。

四项纵深（方案 D4）：
1. 输出脱敏——子进程 stderr/stdout 捕获后经凭据脱敏再入日志/审计
   （复用 quality.diagnostics.redact_diagnostic_text，单一脱敏源）。
2. 配置零透传——spawn env 不含任何 OMNICRAWL_* 配置（由 plugin_sandbox
   _subprocess_env 白名单保证；此处提供校验断言）。
3. temp 写满防护——会话 temp 总量上限（默认 2GB，对齐 N1 commit 上限）→
   超限 E_QUOTA；宿主磁盘剩余空间 <1GB 拒载新会话。
4. 入口 AST 白名单——子进程 import 前模块级静态校验（禁魔法属性链/动态
   import 拼接），与 OS 沙箱叠加。
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path

# 输出脱敏（复用既有单一源，避免两套脱敏规则漂移）
from ..quality.diagnostics import redact_diagnostic_text

# temp 写满防护默认值（方案 D4.5）
TEMP_QUOTA_BYTES_DEFAULT = 2 * 1024 * 1024 * 1024  # 2GB，对齐 N1 commit 上限
HOST_DISK_MIN_FREE_BYTES = 1024 * 1024 * 1024  # 1GB，低于拒载新会话


def redact_subprocess_output(text: str) -> str:
    """子进程 stdout/stderr 入日志/审计前的凭据脱敏（D4.2）。"""
    return redact_diagnostic_text(text)


def assert_no_config_leak(env: dict[str, str]) -> list[str]:
    """配置零透传校验（D4.3）：返回泄漏的 OMNICRAWL_* 键列表（应为空）。"""
    return [key for key in env if key.upper().startswith("OMNICRAWL_") and key != "OMNICRAWL_PLUGIN_SANDBOX"]


def check_host_disk_free(path: Path, min_free_bytes: int = HOST_DISK_MIN_FREE_BYTES) -> bool:
    """宿主磁盘剩余空间检查（D4.5）：<1GB 拒载新会话。"""
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return False
    return usage.free >= min_free_bytes


class TempQuota:
    """会话 temp 总量配额（D4.5）：累计写入超限 → E_QUOTA 语义。"""

    def __init__(self, quota_bytes: int = TEMP_QUOTA_BYTES_DEFAULT) -> None:
        self.quota_bytes = quota_bytes
        self.used_bytes = 0

    def account(self, nbytes: int) -> None:
        """累计写入量；超限抛 QuotaExceededError（调用方映射 E_QUOTA）。"""
        self.used_bytes += nbytes
        if self.used_bytes > self.quota_bytes:
            raise QuotaExceededError(
                f"会话 temp 写入超过配额 {self.quota_bytes} 字节"
                f"（已用 {self.used_bytes}）"
            )


class QuotaExceededError(Exception):
    """temp/网络配额超限（映射协议错误码 E_QUOTA）。"""


# ---- D4.1 入口 AST 白名单（模块级静态校验）----

# 禁魔法属性（防 __class__/__subclasses__ 逃逸链）
_FORBIDDEN_MAGIC_ATTRS = {
    "__class__", "__subclasses__", "__bases__", "__mro__", "__globals__",
    "__code__", "__import__", "__builtins__", "__qualname__",
}


def validate_entry_ast(source: str) -> list[str]:
    """子进程入口模块级静态校验（D4.1）。返回违规描述列表（空 = 通过）。

    - 禁魔法属性链（__class__/__subclasses__ 等逃逸原语）
    - 禁 import 动态拼接（__import__(expr) / importlib.import_module(非常量)）
    注意：这是 OS 沙箱之外的第二道防线，宽松于主仓加载期 _preflight
    （后者管危险调用拦截）；此处只管"逃逸原语"面。
    """
    violations: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"入口源码解析失败: {exc}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_MAGIC_ATTRS:
            violations.append(f"禁用魔法属性: {node.attr}（行 {node.lineno}）")
        if isinstance(node, ast.Call):
            func = node.func
            # __import__(...) 直接调用
            if isinstance(func, ast.Name) and func.id == "__import__":
                violations.append(f"禁用 __import__ 动态调用（行 {node.lineno}）")
            # importlib.import_module(非常量)
            if isinstance(func, ast.Attribute) and func.attr == "import_module":
                if node.args and not isinstance(node.args[0], ast.Constant):
                    violations.append(
                        f"import_module 参数须为常量字面量（行 {node.lineno}）"
                    )
    return violations
