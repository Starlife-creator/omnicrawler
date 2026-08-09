"""环境检测模块。

检测 omnicrawl 框架可用性、版本一致性和磁盘空间。
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path

from ...core.runtime_paths import resolve_cli_command
from ..i18n import _
from ..settings import AppSettings

logger = logging.getLogger(__name__)

# 最小磁盘空间（字节）
MIN_DISK_SPACE = 500 * 1024 * 1024  # 500 MB


class EnvCheckResult:
    """环境检测结果。"""

    def __init__(self) -> None:
        self.omnicrawl_available: bool = False
        self.omnicrawl_path: str = ""
        self.omnicrawl_version: str = ""
        self.errors: list[str] = []
        self.warnings: list[str] = []

    @property
    def is_ok(self) -> bool:
        """环境是否就绪。"""
        return self.omnicrawl_available and len(self.errors) == 0


def check_omnicrawl(command_path: str = "omnicrawl") -> tuple[bool, str]:
    """检查 omnicrawl 命令是否可用。

    F30：冻结模式内置 CLI 存在即视为_("已就绪")强信号——探测只用于取版本号，
    超时/冷启动慢（杀软首扫 1GB+ _internal）不判不可用。
    F31：区分 TimeoutExpired / FileNotFoundError / OSError，记录具体原因。
    F32：Windows 子进程加 CREATE_NO_WINDOW，避免每次探测闪黑控制台窗。

    Args:
        command_path: omnicrawl 命令路径。

    Returns:
        (是否可用, 版本字符串) 元组。
    """
    from ...core.runtime_paths import bundled_cli_path, is_frozen

    resolved_command = resolve_cli_command(command_path)
    bundled = bundled_cli_path()
    # F30/F36：冻结内置 CLI 存在即视为就绪；bundled 存在时探测只取版本号，
    # 用短超时避免在主线程阻塞（冷启动慢不判不可用，由强信号兜底）
    timeout = 10 if bundled is not None else (60 if is_frozen() else 10)
    creationflags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    try:
        result = subprocess.run(
            [resolved_command, "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=creationflags,
        )
        version_output = result.stdout.strip() or result.stderr.strip()
        if result.returncode == 0 and version_output:
            return True, version_output
        if bundled is not None:
            # 内置引擎在但探测异常（慢启动/被杀软拦截）→ 仍按就绪处理
            logger.warning(
                _("内置 CLI 探测返回非零（rc=%s），仍按已就绪处理; 路径: %s"),
                result.returncode, resolved_command,
            )
            return True, version_output or _("（内置引擎版本探测失败）")
        return False, ""
    except subprocess.TimeoutExpired:
        logger.warning(_("omnicrawl 探测超时（%ss），路径: %s"), timeout, resolved_command)
        if bundled is not None:
            return True, _("（内置引擎启动较慢）")
        return False, ""
    except FileNotFoundError as exc:
        logger.warning(_("omnicrawl 命令不存在: %s（%s）"), resolved_command, exc)
        return False, ""
    except OSError as exc:
        logger.warning(_("omnicrawl 探测失败（%s）: %s"), type(exc).__name__, exc)
        return False, ""


def check_disk_space(path: Path) -> tuple[int, int, int]:
    """检查磁盘空间。

    Args:
        path: 要检查的路径。

    Returns:
        (总空间, 已用, 可用) 元组，单位字节。
    """
    try:
        usage = shutil.disk_usage(path)
        return usage.total, usage.used, usage.free
    except Exception:
        return 0, 0, 0


def find_project_root(start_path: Path | None = None) -> Path | None:
    """自动查找 OmniCrawler 项目根目录。

    从 start_path 开始向上搜索包含 pyproject.toml 的目录。

    Args:
        start_path: 起始搜索路径。

    Returns:
        找到的项目根目录路径，未找到返回 None。
    """
    current = Path(start_path) if start_path else Path.cwd()
    for parent in [current] + list(current.parents):
        if (parent / "pyproject.toml").is_file():
            # 检查是否是 omnicrawl 项目
            try:
                content = (parent / "pyproject.toml").read_text(encoding="utf-8")
                if "omnicrawl" in content.lower():
                    return parent
            except Exception:
                logger.debug("Failed to read pyproject.toml for project root detection", exc_info=True)
        # 也检查 setup.py
        if (parent / "setup.py").is_file():
            try:
                content = (parent / "setup.py").read_text(encoding="utf-8")
                if "omnicrawl" in content.lower():
                    return parent
            except Exception:
                logger.debug("Failed to read setup.py for project root detection", exc_info=True)
    return None


def run_full_check(
    omnicrawl_path: str = "omnicrawl",
    project_root: Path | None = None,
) -> EnvCheckResult:
    """运行完整环境检测。

    Args:
        omnicrawl_path: omnicrawl 命令路径。
        project_root: 项目根目录。

    Returns:
        EnvCheckResult 对象。
    """
    result = EnvCheckResult()
    omnicrawl_path = resolve_cli_command(omnicrawl_path)
    result.omnicrawl_path = omnicrawl_path

    # 检查 omnicrawl
    available, version = check_omnicrawl(omnicrawl_path)
    result.omnicrawl_available = available
    result.omnicrawl_version = version

    if not available:
        result.errors.append(
            _(f"无法执行 omnicrawl 命令（路径: {omnicrawl_path}）。") +

            _("请确保 OmniCrawler 框架已安装。")
        )
    else:
        # 版本一致性检查
        settings = AppSettings.instance()
        cached_version = settings.omnicrawl_version
        if cached_version and cached_version != version:
            result.warnings.append(
                _(f"框架版本已变更（{cached_version} → {version}），建议重新验证环境。")
            )
        settings.omnicrawl_version = version

    # 检查磁盘空间
    check_path = project_root or Path.cwd()
    total, used, free = check_disk_space(check_path)
    if total > 0:
        free_mb = free // (1024 * 1024)
        if free < MIN_DISK_SPACE:
            result.warnings.append(_(f"磁盘剩余空间不足 500MB（当前: {free_mb}MB）"))

    return result


def compare_versions(old_version: str, new_version: str) -> str:
    """比较两个版本字符串。

    Args:
        old_version: 旧版本字符串。
        new_version: 新版本字符串。

    Returns:
        'major' - 主版本不同
        'minor' - 次版本不同
        'patch' - 补丁版本不同
        'same' - 版本相同
    """
    def parse(v: str) -> list[int]:
        nums = re.findall(r"\d+", v)
        return [int(n) for n in nums[:3]]

    old_parts = parse(old_version)
    new_parts = parse(new_version)

    # 补齐到 3 位
    while len(old_parts) < 3:
        old_parts.append(0)
    while len(new_parts) < 3:
        new_parts.append(0)

    if old_parts == new_parts:
        return "same"
    if old_parts[0] != new_parts[0]:
        return "major"
    if old_parts[1] != new_parts[1]:
        return "minor"
    return "patch"


def try_auto_install(project_root: Path | None = None) -> tuple[bool, str]:
    """尝试自动安装 OmniCrawler。

    Args:
        project_root: 项目根目录（需包含 setup.py 或 pyproject.toml）。

    Returns:
        (成功与否, 消息) 元组。
    """
    root = project_root or find_project_root()
    if root is None:
        return False, _("未找到 OmniCrawler 项目根目录，请手动指定 omnicrawl 路径")

    has_setup = (root / "setup.py").is_file()
    has_pyproject = (root / "pyproject.toml").is_file()
    if not has_setup and not has_pyproject:
        return False, _(f"目录 {root} 中未找到 setup.py 或 pyproject.toml")

    try:
        # F34：始终用当前解释器 -m pip，避免 PATH 上的 pip 指向错误环境
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", str(root)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(root),
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
        if result.returncode == 0:
            # 验证安装
            available, version = check_omnicrawl()
            if available:
                return True, _(f"安装成功！omnicrawl {version}")
            return False, _("安装完成但 omnicrawl 命令仍不可用，请检查 PATH 环境变量")
        else:
            return False, _(f"安装失败: {result.stderr.strip()[-500:]}")
    except Exception as e:
        return False, _(f"安装异常: {e}")
