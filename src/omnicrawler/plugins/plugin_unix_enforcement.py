"""Standard-library-only Unix confinement for the plugin host.

``apply_linux`` is a one-way, in-process operation and must be called *before*
loading a plugin. It combines Landlock with a seccomp deny filter: Landlock
confines file access, while seccomp prevents direct sockets, child processes,
and attempts to remove either protection.

``darwin_launch`` wraps the already-resolved sandbox-host command with
``sandbox-exec``. This module deliberately has no dependency on the host
package, so its Linux entry point remains usable in the frozen host.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

E_UNSUPPORTED_ENV = "E_UNSUPPORTED_ENV"
_PR_SET_NO_NEW_PRIVS = 38
_PR_SET_SECCOMP = 22
_SECCOMP_SET_MODE_FILTER = 1
_RET_KILL = 0x80000000
_RET_ERRNO = 0x00050001
_RET_ALLOW = 0x7FFF0000
_LANDLOCK_ACCESS_V1 = (1 << 14) - 1
_BPF_LD = 0
_BPF_JMP = 0x05
_BPF_JEQ = 0x10
_BPF_JSET = 0x40
_BPF_K = 0x00  # immediate constant in BPF_LD/JMP/RET, not the RET register class
_BPF_RET = 6


def _syscall_number(name: str) -> int:
    machine = os.uname().machine
    if machine not in ("x86_64", "aarch64"):
        raise RuntimeError(f"{E_UNSUPPORTED_ENV}: unsupported seccomp architecture {machine}")
    numbers = {
        "socket": (41, 198), "socketpair": (53, 199), "ptrace": (101, 117),
        "bpf": (321, 280), "process_vm_readv": (310, 270),
        "process_vm_writev": (311, 271), "kexec_load": (246, 104),
    }
    return numbers[name][0 if machine == "x86_64" else 1]


def _landlock_abi(libc: Any) -> int:
    """Query ABI using the kernel's NULL/zero/1 version-query contract."""
    import ctypes

    ctypes.set_errno(0)
    return libc.syscall(444, ctypes.c_void_p(), ctypes.c_size_t(0), ctypes.c_uint(1))


def linux_capability() -> bool:
    """Probe real v1+ Landlock and seccomp availability without enforcing them."""
    import ctypes

    if sys.platform != "linux" or os.uname().machine not in ("x86_64", "aarch64"):
        return False
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall.restype = ctypes.c_long
        abi = _landlock_abi(libc)
        if abi < 1:
            return False
        # This changes no process state; PR_GET_SECCOMP only reads the mode.
        return _prctl(libc, 21, 0) >= 0
    except (OSError, AttributeError, TypeError):
        return False


def _prctl(libc: Any, option: int, arg2: Any = 0, arg3: Any = 0) -> int:
    import ctypes

    number = 157 if os.uname().machine == "x86_64" else 167

    def argument(value: Any) -> Any:
        return ctypes.c_ulong(value) if isinstance(value, int) else value

    return libc.syscall(number, option, argument(arg2), argument(arg3), 0, 0)


def _sock_filter() -> Any:
    """Kill unknown ABIs and deny network/cross-process/kernel attack surfaces."""
    import ctypes
    import struct

    class Filter(ctypes.Structure):
        _fields_ = [
            ("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte),
            ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint32),
        ]

    class Program(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(Filter))]

    machine = os.uname().machine
    audit = 62 if machine == "x86_64" else 183
    # seccomp_data.nr and arch are fixed by the kernel ABI on both supported CPUs.
    instructions = [
        (_BPF_LD | _BPF_K, 0, 0, 0),
        # x32 syscall numbers have this bit; never confuse them with native ABI.
        (_BPF_JMP | _BPF_JSET | _BPF_K, 0x40000000, 5, 0),
        (_BPF_RET | _BPF_K, 0, 0, _RET_ALLOW),
        (_BPF_LD | _BPF_K, 0, 0, 4),
        (_BPF_JMP | _BPF_JEQ | _BPF_K, audit, 0, 1),
        (_BPF_RET | _BPF_K, 0, 0, _RET_KILL),
        (_BPF_LD | _BPF_K, 0, 0, 0),
    ]
    instructions.extend(
        (_BPF_JMP | _BPF_JEQ | _BPF_K, _syscall_number(name), 0, 1)
        for name in (
            "socket", "socketpair", "ptrace", "bpf", "process_vm_readv",
            "process_vm_writev", "kexec_load",
        )
    )
    instructions.extend([
        (_BPF_RET | _BPF_K, 0, 0, _RET_ERRNO),
        (_BPF_RET | _BPF_K, 0, 0, _RET_ALLOW),
    ])
    if struct.calcsize("P") != 8:
        raise RuntimeError(f"{E_UNSUPPORTED_ENV}: seccomp requires a 64-bit interpreter")
    array = (Filter * len(instructions))(
        *(Filter(code, jt, jf, k) for code, jt, jf, k in instructions)
    )
    return array, Program(len(array), array)


def _landlock(access_fs: int, paths: Sequence[Path]) -> None:
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long

    class Attr(ctypes.Structure):
        # Keep the stable ABI v1 prefix; later kernels accept this short layout.
        _fields_ = [("handled_access_fs", ctypes.c_uint64)]

    class PathBeneath(ctypes.Structure):
        _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]

    abi = _landlock_abi(libc)
    if abi < 1:
        raise OSError(ctypes.get_errno(), "Landlock ABI unavailable")
    handled = access_fs & _LANDLOCK_ACCESS_V1
    buffer = ctypes.create_string_buffer(ctypes.sizeof(Attr))
    attr = Attr.from_buffer(buffer)
    attr.handled_access_fs = handled
    ctypes.set_errno(0)
    ruleset = libc.syscall(445, ctypes.byref(buffer), ctypes.c_size_t(ctypes.sizeof(buffer)), ctypes.c_uint(0))
    if ruleset < 0:
        raise OSError(ctypes.get_errno(), "Landlock ruleset creation failed")
    try:
        for path in paths:
            fd = os.open(path, getattr(os, "O_PATH", os.O_RDONLY) | getattr(os, "O_CLOEXEC", 0))
            try:
                rule = PathBeneath(handled, fd)
                if libc.syscall(446, ruleset, 1, ctypes.byref(rule), 0):
                    raise OSError(ctypes.get_errno(), f"Landlock rule rejected: {path}")
            finally:
                os.close(fd)
        if _prctl(libc, _PR_SET_NO_NEW_PRIVS, 1):
            raise OSError(ctypes.get_errno(), "no_new_privs failed")
        if libc.syscall(447, ruleset, 0):
            raise OSError(ctypes.get_errno(), "Landlock enforcement failed")
    finally:
        os.close(ruleset)


def _standard_library_roots() -> tuple[Path, ...]:
    roots = {Path(path).resolve() for path in sys.path if path and Path(path).is_dir()}
    roots.update(Path(value).resolve() for value in (sys.prefix, sys.base_prefix) if value)
    return tuple(sorted(roots, key=str))


def _linux_runtime_roots() -> tuple[Path, ...]:
    return tuple(
        Path(value) for value in (
            "/lib", "/lib64", "/usr/lib", "/usr/local/lib",
            "/etc/ssl", "/etc/localtime", "/usr/share/zoneinfo",
        ) if Path(value).exists()
    )


def apply_linux(*, plugin_root: Path, verified_entry_dir: Path | None = None) -> str:
    """Install irreversible file and socket confinement before plugin import.

    A failure is fail-closed: the caller must not load plugin code. Read-only
    access is limited to interpreter/standard-library roots, the verified
    entry, and the plugin itself. No writable directory is exposed; files and
    artifacts remain capability-proxy operations.
    """
    if sys.platform != "linux":
        raise RuntimeError(f"{E_UNSUPPORTED_ENV}: Landlock requires Linux")
    roots = (*_standard_library_roots(), *_linux_runtime_roots(), plugin_root.resolve())
    if verified_entry_dir is not None:
        roots += (verified_entry_dir.resolve(),)
    _landlock(_LANDLOCK_ACCESS_V1, roots)
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    if _prctl(libc, _PR_SET_NO_NEW_PRIVS, 1):
        raise OSError(ctypes.get_errno(), "no_new_privs failed")
    array, program = _sock_filter()
    if _prctl(libc, _PR_SET_SECCOMP, _SECCOMP_SET_MODE_FILTER, ctypes.byref(program)):
        raise OSError(ctypes.get_errno(), "seccomp filter installation failed")
    # Keep the BPF program alive for the interpreter lifetime.
    globals()["_installed_filter"] = (array, program)
    return "landlock+seccomp"


def _quote(path: Path) -> str:
    return '"' + str(path.resolve()).replace("\\", "\\\\").replace('"', '\\"') + '"'


def darwin_profile(*, plugin_root: Path, verified_entry_dir: Path | None = None) -> str:
    """Return a fail-closed Seatbelt profile with no direct network access."""
    roots = (*_standard_library_roots(), plugin_root.resolve())
    if verified_entry_dir is not None:
        roots += (verified_entry_dir.resolve(),)
    allow = "\n".join(f"  (subpath {_quote(root)})" for root in roots)
    return f"""(version 1)
(deny default)
(allow signal (target self))
(allow file-read* file-execute
{allow}
  (subpath "/usr/lib")
  (subpath "/usr/bin")
  (subpath "/bin")
  (subpath "/System/Library")
  (subpath "/Library/Apple")
  (subpath "/Library/Frameworks/Python.framework")
  (subpath "/private/var/db/dyld")
  (subpath "/dev"))
(allow file-read-metadata)
(allow file-write* (literal "/dev/null"))
(deny network*)
(deny process-exec*)
"""


@contextmanager
def darwin_launch(
    command: Sequence[str], *, plugin_root: Path, verified_entry_dir: Path | None = None
) -> Iterator[list[str]]:
    """Yield a ``sandbox-exec`` command and clean up its private profile."""
    executable = shutil.which("sandbox-exec")
    if executable is None:
        raise RuntimeError(f"{E_UNSUPPORTED_ENV}: sandbox-exec unavailable")
    profile = darwin_profile(plugin_root=plugin_root, verified_entry_dir=verified_entry_dir)
    descriptor, name = tempfile.mkstemp(prefix="omnicrawler-seatbelt-", suffix=".sb")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(profile)
        yield [executable, "-f", name, *command]
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass
