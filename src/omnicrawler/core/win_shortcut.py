"""Windows 首启快捷方式（I2）—— 纯 ``ctypes`` 的 ``IShellLinkW`` + ``IPersistFile``。

为什么不用 pywin32 / PowerShell / 手写 .lnk
------------------------------------------

* **pywin32**：多一个运行时依赖，还要在 PyInstaller 里收 ``pythoncom*.dll`` 与
  hidden imports —— 正是 W4.2 那种"构建期报绿、干净机器报红"的风险面。
* **子进程调 PowerShell / .vbs**：外部进程写文件、断言绕远，且脚本宿主常被杀软拦。
* **手写 MS-SHLLINK 二进制**：可跨平台断言字节，但**语义仍只能在 Windows 验证**，
  没有收益。

``ole32`` / ``shell32`` 是系统 DLL，零新依赖。

★ 正确性只能靠"读回断言"，不能靠"文件存在"
------------------------------------------

ctypes 误用（vtable 槽位错、签名错）是 **AV 崩进程**，不是 Python 异常，
``try/except`` 兜不住。所以：

1. **CI 在 Windows 上读回**：创建后用 ``GetPath`` / ``GetWorkingDirectory`` /
   ``GetIconLocation`` 把字段读回来逐字比对（见 ``read_back``）；
   "文件存在" **不算**通过。
2. 运行期只保证"一个可选功能不拖垮主流程"：调用方全包 ``except Exception``，
   失败 → Toast + 日志，**不重试、不静默、不假装成功**。
"""

from __future__ import annotations

import ctypes
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── 固定 GUID（Windows SDK） ───────────────────────────────────────────────
_CLSID_SHELL_LINK = "{00021401-0000-0000-C000-000000000046}"
_IID_SHELL_LINK_W = "{000214F9-0000-0000-C000-000000000046}"
_IID_PERSIST_FILE = "{0000010B-0000-0000-C000-000000000046}"

_CLSCTX_INPROC_SERVER = 0x1
_COINIT_APARTMENTTHREADED = 0x2
_STGM_READ = 0x0
# CoInitializeEx 在"本线程已被 Qt/其他库按另一种模式初始化过"时返回它。
# 这不是错误：COM 已经可用，只是别去 CoUninitialize。
_RPC_E_CHANGED_MODE = -2147417850
_S_OK = 0
_S_FALSE = 1

# ── vtable 槽位（IUnknown 占 0..2） ────────────────────────────────────────
# ★ 必须按**接口声明的完整顺序**数，漏一个都会静默调到别的函数上（然后 AV）。
# IShellLinkW 在 IUnknown 之后有 18 个方法，完整顺序是：
#   3 GetPath / 4 GetIDList / 5 SetIDList / 6 GetDescription / 7 SetDescription /
#   8 GetWorkingDirectory / 9 SetWorkingDirectory / 10 GetArguments / 11 SetArguments /
#   12 GetHotkey / 13 SetHotkey / 14 GetShowCmd / 15 SetShowCmd /
#   16 GetIconLocation / 17 SetIconLocation / 18 SetRelativePath / 19 Resolve / 20 SetPath
_VT_QUERY_INTERFACE = 0
_VT_RELEASE = 2
_VT_GET_PATH = 3
_VT_SET_DESCRIPTION = 7
_VT_GET_WORKING_DIRECTORY = 8
_VT_SET_WORKING_DIRECTORY = 9
_VT_GET_ICON_LOCATION = 16
_VT_SET_ICON_LOCATION = 17
_VT_SET_PATH = 20
# IPersistFile : IPersist : IUnknown → GetClassID(3) IsDirty(4) Load(5) Save(6)
_VT_PERSIST_LOAD = 5
_VT_PERSIST_SAVE = 6

# CSIDL（shell32.SHGetFolderPathW）。用 shell API 而不是 `%USERPROFILE%\Desktop`：
# 桌面常被 OneDrive 重定向，硬拼路径会在那些机器上静默落到错误位置。
_CSIDL_PROGRAMS = 0x0002
_CSIDL_DESKTOPDIRECTORY = 0x0010

_MAX_PATH = 260
_LONG_PATH = 1024

# 只有 Windows 的 ctypes 才有这两个名字；Linux 上取到 None（mypy 也据此不报
# "module has no attribute"，因为 mypy 常跑在 Linux runner 上）。
_WINFUNCTYPE: Any = getattr(ctypes, "WINFUNCTYPE", None)


@dataclass(frozen=True)
class ShortcutResult:
    """一个可选功能的结果，刻意做成"可打印、可断言"的结构化对象。"""

    ok: bool
    status: str  # "created" | "unsupported" | "failed"
    detail: str = ""
    created: tuple[Path, ...] = field(default=())


def is_platform_supported() -> bool:
    """UI 可见性判据：只有 Windows 才提供这个可选功能。

    刻意**不含** ``is_frozen()``：源码运行时 UI 仍然要能显示/测试这个入口，
    能不能真的创建由 :func:`resolve_target` 决定（没有打包入口就返回 None）。
    """
    return sys.platform == "win32"


class _GUID(ctypes.Structure):
    _fields_ = (
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    )


def _libraries() -> tuple[Any, Any]:
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    ole32.CLSIDFromString.argtypes = (ctypes.c_wchar_p, ctypes.POINTER(_GUID))
    ole32.CLSIDFromString.restype = ctypes.c_long
    ole32.CoInitializeEx.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoUninitialize.argtypes = ()
    ole32.CoUninitialize.restype = None
    ole32.CoCreateInstance.argtypes = (
        ctypes.POINTER(_GUID),
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(_GUID),
        ctypes.POINTER(ctypes.c_void_p),
    )
    ole32.CoCreateInstance.restype = ctypes.c_long
    shell32.SHGetFolderPathW.argtypes = (
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_wchar_p,
    )
    shell32.SHGetFolderPathW.restype = ctypes.c_long
    return ole32, shell32


def _guid(text: str) -> _GUID:
    ole32, _ = _libraries()
    guid = _GUID()
    hr = ole32.CLSIDFromString(text, ctypes.byref(guid))
    if hr != _S_OK:
        raise OSError(f"CLSIDFromString failed for {text}: {hr:#x}")
    return guid


def _method(ptr: Any, index: int, *argtypes: Any) -> Any:
    """按 vtable 槽位取一个**签名已声明**的函数指针。

    签名必须逐参数正确：ctypes 误用会 AV 崩进程，不是 Python 异常。
    """
    vtable = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    prototype = _WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
    return prototype(vtable[index])


def _release(ptr: Any) -> None:
    """IUnknown::Release —— 无参数，返回引用计数（我们不看返回值）。"""
    if ptr:
        try:
            _method(ptr, _VT_RELEASE)(ptr)
        except OSError:  # pragma: no cover - Release 失败无可挽回，也不该影响主流程
            logger.debug("IShellLinkW::Release failed", exc_info=True)


def _new_shell_link() -> Any:
    """CoCreateInstance(CLSID_ShellLink) → 一个 IShellLinkW 指针。"""
    ole32, _ = _libraries()
    shell_link = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(
        ctypes.byref(_guid(_CLSID_SHELL_LINK)),
        None,
        _CLSCTX_INPROC_SERVER,
        ctypes.byref(_guid(_IID_SHELL_LINK_W)),
        ctypes.byref(shell_link),
    )
    if hr != _S_OK or not shell_link:
        raise OSError(f"CoCreateInstance(ShellLink) failed: {hr:#x}")
    return shell_link


def _persist_file_of(shell_link: Any) -> Any:
    """QueryInterface(IID_IPersistFile) —— 同一个对象上的另一个接口。"""
    persist = ctypes.c_void_p()
    query = _method(
        shell_link, _VT_QUERY_INTERFACE,
        ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p),
    )
    hr = query(shell_link, ctypes.byref(_guid(_IID_PERSIST_FILE)), ctypes.byref(persist))
    if hr != _S_OK or not persist:
        raise OSError(f"QueryInterface(IPersistFile) failed: {hr:#x}")
    return persist


def _write_fields(
    shell_link: Any, target: Path, *, working_dir: Path | None, icon: str | None, description: str
) -> None:
    hr = _method(shell_link, _VT_SET_PATH, ctypes.c_wchar_p)(shell_link, str(target))
    if hr != _S_OK:
        raise OSError(f"IShellLinkW::SetPath failed: {hr:#x}")
    if description:
        hr = _method(shell_link, _VT_SET_DESCRIPTION, ctypes.c_wchar_p)(shell_link, description)
        if hr != _S_OK:
            raise OSError(f"IShellLinkW::SetDescription failed: {hr:#x}")
    if working_dir is not None:
        set_working = _method(shell_link, _VT_SET_WORKING_DIRECTORY, ctypes.c_wchar_p)
        hr = set_working(shell_link, str(working_dir))
        if hr != _S_OK:
            raise OSError(f"IShellLinkW::SetWorkingDirectory failed: {hr:#x}")
    if icon:
        # pszIconPath 里可以带 ",<index>" 后缀（典型写法：<exe>,0）
        set_icon = _method(shell_link, _VT_SET_ICON_LOCATION, ctypes.c_wchar_p, ctypes.c_int)
        hr = set_icon(shell_link, icon, 0)
        if hr != _S_OK:
            raise OSError(f"IShellLinkW::SetIconLocation failed: {hr:#x}")


def create_shortcut(
    link_path: Path,
    target: Path,
    *,
    working_dir: Path | None = None,
    icon: str | None = None,
    description: str = "OmniCrawler",
) -> ShortcutResult:
    """在 *link_path* 写一个指向 *target* 的 ``.lnk``。

    低层入口：**路径完全由调用方给定**（测试因此可以只写 ``tmp_path``，
    永远不碰用户真实的桌面）。
    """
    if not is_platform_supported():
        return ShortcutResult(ok=False, status="unsupported", detail="Windows only")
    if not target.is_file():
        return ShortcutResult(
            ok=False, status="failed", detail=f"shortcut target does not exist: {target}"
        )

    ole32, _ = _libraries()
    co_initialized = False
    hr = ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
    if hr in (_S_OK, _S_FALSE):
        co_initialized = True
    elif hr != _RPC_E_CHANGED_MODE:
        return ShortcutResult(ok=False, status="failed", detail=f"CoInitializeEx failed: {hr:#x}")

    shell_link: Any = None
    persist: Any = None
    try:
        shell_link = _new_shell_link()
        _write_fields(
            shell_link, target, working_dir=working_dir, icon=icon, description=description
        )
        persist = _persist_file_of(shell_link)
        link_path.parent.mkdir(parents=True, exist_ok=True)
        save = _method(persist, _VT_PERSIST_SAVE, ctypes.c_wchar_p, ctypes.c_int)
        hr = save(persist, str(link_path), 1)  # fRemember = TRUE
        if hr != _S_OK:
            return ShortcutResult(ok=False, status="failed", detail=f"IPersistFile::Save failed: {hr:#x}")
    except (OSError, AttributeError, ValueError) as exc:
        # 刻意不吞 BaseException：ctypes 误用是 AV（进程级），try/except 兜不住 ——
        # 所以正确性仍然靠 CI 的读回断言，而不是靠这段兜底。
        logger.warning("create_shortcut failed: %s", exc)
        return ShortcutResult(ok=False, status="failed", detail=str(exc))
    finally:
        _release(persist)
        _release(shell_link)
        if co_initialized:
            ole32.CoUninitialize()

    return ShortcutResult(ok=True, status="created", detail=str(link_path), created=(link_path,))


def read_back(link_path: Path) -> dict[str, str]:
    """把 ``.lnk`` 里的 target / working_dir / icon **读回来**。

    CI 的正确性判据就是这个：写进去再读出来逐字比对。
    "文件存在" 不算通过（红线：看起来正常 ≠ 运行时正确）。
    """
    if not is_platform_supported():
        raise RuntimeError("read_back is Windows-only")
    ole32, _ = _libraries()
    co_initialized = False
    hr = ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
    if hr in (_S_OK, _S_FALSE):
        co_initialized = True
    elif hr != _RPC_E_CHANGED_MODE:
        raise OSError(f"CoInitializeEx failed: {hr:#x}")

    shell_link: Any = None
    persist: Any = None
    try:
        shell_link = _new_shell_link()
        persist = _persist_file_of(shell_link)
        load = _method(persist, _VT_PERSIST_LOAD, ctypes.c_wchar_p, ctypes.c_ulong)
        hr = load(persist, str(link_path), _STGM_READ)
        if hr != _S_OK:
            raise OSError(f"IPersistFile::Load failed for {link_path}: {hr:#x}")

        path_buf = ctypes.create_unicode_buffer(_LONG_PATH)
        get_path = _method(
            shell_link, _VT_GET_PATH,
            ctypes.c_wchar_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong,
        )
        # pfd 传 NULL（该参数可选）；fFlags=SLGP_RAWPATH(4) 要原始路径，不要 8.3 短名
        hr = get_path(shell_link, path_buf, _LONG_PATH, None, 4)
        if hr != _S_OK:
            raise OSError(f"IShellLinkW::GetPath failed: {hr:#x}")

        dir_buf = ctypes.create_unicode_buffer(_LONG_PATH)
        get_dir = _method(
            shell_link, _VT_GET_WORKING_DIRECTORY, ctypes.c_wchar_p, ctypes.c_int
        )
        hr = get_dir(shell_link, dir_buf, _LONG_PATH)
        if hr != _S_OK:
            raise OSError(f"IShellLinkW::GetWorkingDirectory failed: {hr:#x}")

        icon_buf = ctypes.create_unicode_buffer(_LONG_PATH)
        icon_index = ctypes.c_int(0)
        get_icon = _method(
            shell_link, _VT_GET_ICON_LOCATION,
            ctypes.c_wchar_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int),
        )
        hr = get_icon(shell_link, icon_buf, _LONG_PATH, ctypes.byref(icon_index))
        if hr != _S_OK:
            raise OSError(f"IShellLinkW::GetIconLocation failed: {hr:#x}")
    finally:
        _release(persist)
        _release(shell_link)
        if co_initialized:
            ole32.CoUninitialize()

    return {
        "target": path_buf.value,
        "working_dir": dir_buf.value,
        "icon": icon_buf.value,
        "icon_index": str(icon_index.value),
    }


def _known_folder(csidl: int) -> Path | None:
    """shell32.SHGetFolderPathW —— 尊重 OneDrive 之类的重定向。"""
    _, shell32 = _libraries()
    buffer = ctypes.create_unicode_buffer(_MAX_PATH)
    if shell32.SHGetFolderPathW(None, csidl, None, 0, buffer) != _S_OK or not buffer.value:
        return None
    return Path(buffer.value)


def default_shortcut_paths() -> tuple[Path, ...]:
    """桌面 + 开始菜单，都是 **per-user** ⇒ 不需要提权（绿色便携的红线之一）。"""
    paths: list[Path] = []
    desktop = _known_folder(_CSIDL_DESKTOPDIRECTORY)
    if desktop is not None:
        paths.append(desktop / "OmniCrawler.lnk")
    programs = _known_folder(_CSIDL_PROGRAMS)
    if programs is not None:
        paths.append(programs / "OmniCrawler.lnk")
    return tuple(paths)


def resolve_target() -> Path | None:
    """快捷方式要指向的入口：**打包后的** ``OmniCrawler.exe``。

    源码运行（``is_frozen()`` 为假）时没有可指的 exe ⇒ 返回 None（不弹、不建）。
    """
    from .runtime_paths import application_dir, is_frozen

    if not is_frozen():
        return None
    candidate = application_dir() / "OmniCrawler.exe"
    return candidate if candidate.is_file() else None


def create_shortcut_for_app(
    destinations: Sequence[Path] | None = None,
) -> ShortcutResult:
    """给当前应用创建快捷方式（运行期入口）。

    * ``WorkingDirectory`` = exe 所在目录（``OmniCrawler-Launcher.bat`` 用
      ``cd /d "%~dp0"`` 做过同一件事，有先例）
    * ``IconLocation`` = ``<exe>,0`` —— 与 spec 的 ``icon=`` 是**同一份资源**，
      两处一致性天然成立

    失败**不重试、不静默**：返回结构化结果，由调用方给 Toast + 日志。
    """
    if not is_platform_supported():
        return ShortcutResult(ok=False, status="unsupported", detail="Windows only")
    target = resolve_target()
    if target is None:
        return ShortcutResult(
            ok=False, status="unsupported", detail="no packaged entry point (source run)"
        )
    if destinations is None:
        destinations = default_shortcut_paths()
        if not destinations:
            return ShortcutResult(
                ok=False, status="failed", detail="could not resolve any shortcut folder"
            )

    created: list[Path] = []
    failures: list[str] = []
    for destination in destinations:
        result = create_shortcut(
            destination,
            target,
            working_dir=target.parent,
            icon=f"{target},0",
            description="OmniCrawler",
        )
        if result.ok:
            created.append(destination)
        else:
            failures.append(f"{destination.name}: {result.detail}")

    if created and not failures:
        return ShortcutResult(
            ok=True, status="created", detail="; ".join(str(p) for p in created), created=tuple(created)
        )
    if created:
        return ShortcutResult(
            ok=False, status="failed", detail="; ".join(failures), created=tuple(created)
        )
    return ShortcutResult(ok=False, status="failed", detail="; ".join(failures) or "no destination")
