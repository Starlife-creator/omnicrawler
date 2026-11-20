"""本地导入浏览器内核的**版本探测**与驱动匹配提示（§15.16.6）。

## 为什么必须要探测

维护者要求支持「导入自己的浏览器内核」。但有一个绕不过去的生态约束：

> Selenium 系的 **chromedriver 主版本必须与 Chrome 主版本完全一致**。

这不能靠代码抹平。所以导入流程不能只让用户选一个 `.exe` 就完事 ——
必须**探测出版本**，然后明确告诉用户「还需要匹配版本的驱动」。

把约束如实提前告知，比等运行失败才报错好得多。

## 两条探测路径，为什么优先读文件元数据

| 方式 | 优点 | 缺点 |
|---|---|---|
| 执行 `<exe> --version` | 通用 | **要启动进程**；沙箱/无窗口环境下会失败 |
| 读可执行文件的版本资源 | 零副作用、快 | 只在 Windows / macOS bundle 上有 |

实测（2026-11-20）：`chrome.exe --version` 在受限环境返回
`退出码 4294930433` + `TransactNamedPipe: 管道已结束`（crashpad 起不来），
而读文件元数据直接拿到 `149.0.7827.55`。

所以顺序是：**先读元数据，失败才尝试执行**。执行放后面，是因为它可能
真的把浏览器拉起来 —— 那不该是导入流程的默认代价。

## 一个容易混的区分

Playwright 的目录名 `chromium-1228` 里的 `1228` 是**构建号**，
不是浏览器版本（真实版本是 `149.0.7827.55`）。两者用途不同：
构建号标识 Playwright 的下载包，版本号决定驱动兼容性。
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

#: 从输出里提取主版本的常见形态。
#:
#: Chrome/Chromium: `Google Chrome 149.0.7827.55`
#: Firefox:         `Mozilla Firefox 133.0.3`
#: Edge:            `Microsoft Edge 149.0.7827.55`
_VERSION_PATTERN = re.compile(r"(\d+\.\d+(?:\.\d+){0,2})")

#: 内核族 → 需要的驱动名。
_DRIVER_FOR_ENGINE = {
    "chromium": "chromedriver",
    "firefox": "geckodriver",
    "webkit": "",
}


@dataclass(frozen=True, slots=True)
class KernelProbe:
    """一次本地内核探测的结果。"""

    path: Path
    #: 探测到的版本号（`149.0.7827.55`）；未探测到时为空。
    version: str = ""
    #: 主版本号（`149`），驱动匹配只看它。
    major: int = 0
    #: 推测的内核族（chromium / firefox / webkit / 空）。
    engine_family: str = ""
    #: 探测方式（文件元数据 / 执行 --version / 文件名推断）。
    detected_by: str = ""
    #: 需要的驱动名（空 = 不需要或未知）。
    required_driver: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return bool(self.version)

    def driver_hint(self) -> str:
        """给用户的可执行提示。"""
        if not self.required_driver:
            return ""
        if not self.major:
            return (
                f"需要与内核版本匹配的 {self.required_driver}；"
                "但版本未能探测，请手动确认主版本一致"
            )
        return (
            f"需要 {self.required_driver} {self.major}.x"
            f"（主版本必须与内核完全一致，否则无法通信）"
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "version": self.version,
            "major": self.major,
            "engine_family": self.engine_family,
            "detected_by": self.detected_by,
            "required_driver": self.required_driver,
            "warnings": list(self.warnings),
            "driver_hint": self.driver_hint(),
        }


def _engine_from_name(name: str) -> str:
    """从文件名/路径推断内核族。"""
    lowered = name.lower()
    if "firefox" in lowered or "gecko" in lowered:
        return "firefox"
    if "webkit" in lowered or "safari" in lowered:
        return "webkit"
    if any(token in lowered for token in ("chrome", "chromium", "msedge", "edge")):
        return "chromium"
    return ""


def _version_from_file_metadata(path: Path) -> str:
    """读可执行文件的版本资源（Windows / macOS bundle）。"""
    if os.name != "nt":
        return ""
    try:
        import ctypes  # noqa: PLC0415 - 仅 Windows 需要

        # 用 ctypes 读 VS_VERSION_INFO：不依赖 pywin32（项目零第三方依赖）。
        windll = ctypes.windll  # type: ignore[attr-defined]  # 仅 Windows 存在
        size = windll.version.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return ""
        buffer = ctypes.create_string_buffer(size)
        if not windll.version.GetFileVersionInfoW(str(path), 0, size, buffer):
            return ""
        pointer = ctypes.c_void_p()
        length = ctypes.c_uint()
        if not windll.version.VerQueryValueW(
            buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)
        ):
            return ""

        class _FixedFileInfo(ctypes.Structure):
            _fields_ = [
                ("dwSignature", ctypes.c_uint32),
                ("dwStrucVersion", ctypes.c_uint32),
                ("dwFileVersionMS", ctypes.c_uint32),
                ("dwFileVersionLS", ctypes.c_uint32),
                ("dwProductVersionMS", ctypes.c_uint32),
                ("dwProductVersionLS", ctypes.c_uint32),
                ("dwFileFlagsMask", ctypes.c_uint32),
                ("dwFileFlags", ctypes.c_uint32),
                ("dwFileOS", ctypes.c_uint32),
                ("dwFileType", ctypes.c_uint32),
                ("dwFileSubtype", ctypes.c_uint32),
                ("dwFileDateMS", ctypes.c_uint32),
                ("dwFileDateLS", ctypes.c_uint32),
            ]

        info = ctypes.cast(pointer, ctypes.POINTER(_FixedFileInfo)).contents
        major = (info.dwFileVersionMS >> 16) & 0xFFFF
        minor = info.dwFileVersionMS & 0xFFFF
        build = (info.dwFileVersionLS >> 16) & 0xFFFF
        patch = info.dwFileVersionLS & 0xFFFF
        if major == 0 and minor == 0 and build == 0 and patch == 0:
            return ""
        return f"{major}.{minor}.{build}.{patch}"
    except Exception:  # noqa: BLE001 - 探测失败退化为下一条路径
        return ""


def _version_by_running(path: Path, timeout: float = 20.0) -> str:
    """执行 `<exe> --version` 取版本。

    **放在最后尝试**：它真的会拉起浏览器进程，在受限环境里可能失败
    （实测 `TransactNamedPipe: 管道已结束`）。
    """
    try:
        completed = subprocess.run(  # noqa: S603 - 用户显式指定的可执行文件
            [str(path), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env={**os.environ, "PYTHONUTF8": "1"},
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    text = f"{completed.stdout or ''}\n{completed.stderr or ''}".strip()
    match = _VERSION_PATTERN.search(text)
    return match.group(1) if match else ""


def probe_kernel(path: str | Path) -> KernelProbe:
    """探测一个本地浏览器内核。

    探测失败**不抛异常** —— 导入流程应当能把「版本未知」作为一个
    明确状态展示给用户，而不是崩掉。
    """
    target = Path(path)
    warnings: list[str] = []

    # **先判内核族，再判文件是否存在**。
    # 内核族只取决于文件名/路径，与文件在不在无关 ——
    # 早先文件不存在就提前 return，导致「要哪个驱动」这个信息也一起丢了，
    # 而用户恰恰是在**还没拿到文件**时最需要知道这个。
    engine = _engine_from_name(target.name) or _engine_from_name(target.parent.name)
    required_driver = _DRIVER_FOR_ENGINE.get(engine, "")

    if not target.is_file():
        return KernelProbe(
            path=target,
            engine_family=engine,
            required_driver=required_driver,
            warnings=("文件不存在",),
        )
    if os.name != "nt" and not os.access(target, os.X_OK):
        warnings.append("没有执行权限")

    version = _version_from_file_metadata(target)
    detected_by = "文件元数据" if version else ""

    if not version:
        version = _version_by_running(target)
        detected_by = "执行 --version" if version else ""

    if not version:
        # 最后兜底：从路径里找版本号（如 `chrome-131/`）
        match = _VERSION_PATTERN.search(target.as_posix())
        if match:
            version = match.group(1)
            detected_by = "路径推断"
            warnings.append("版本来自路径推断，可能不准")

    major = 0
    if version:
        head = version.split(".", 1)[0]
        major = int(head) if head.isdigit() else 0
    else:
        warnings.append("未能探测版本 —— 无法判断驱动是否匹配")

    if not engine:
        warnings.append("未能判断内核族 —— 请确认它是 Chrome / Firefox / WebKit")

    return KernelProbe(
        path=target,
        version=version,
        major=major,
        engine_family=engine,
        detected_by=detected_by,
        required_driver=required_driver,
        warnings=tuple(warnings),
    )
