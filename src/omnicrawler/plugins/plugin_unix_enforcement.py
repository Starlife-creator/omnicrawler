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
import subprocess
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
# ABI v1 includes the full file/access set through REFER (bit 13).
_LANDLOCK_ACCESS_V1 = (1 << 13) - 1
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
    audit = 0xC000003E if machine == "x86_64" else 0xC00000B7
    if sys.platform != "linux" or machine not in ("x86_64", "aarch64"):
        raise RuntimeError(f"{E_UNSUPPORTED_ENV}: seccomp requires a supported Linux architecture")
    # seccomp_data.nr and arch are fixed by the kernel ABI on both supported CPUs.
    instructions = [
        (_BPF_LD | 0x20 | _BPF_K, 0, 0, 4),
        (_BPF_JMP | _BPF_JEQ | _BPF_K, 0, 1, audit),
        (_BPF_RET | _BPF_K, 0, 0, _RET_KILL),
        (_BPF_LD | 0x20 | _BPF_K, 0, 0, 0),
        # x32 syscall numbers have this bit; never confuse them with native ABI.
        (_BPF_JMP | _BPF_JSET | _BPF_K, 0, 1, 0x40000000),
        (_BPF_RET | _BPF_K, 0, 0, _RET_KILL),
    ]
    names = (
        "socket", "socketpair", "ptrace", "bpf", "process_vm_readv",
        "process_vm_writev", "kexec_load",
    )
    instructions.extend(
        (_BPF_JMP | _BPF_JEQ | _BPF_K, len(names) - index - 1, 0, _syscall_number(name))
        for index, name in enumerate(names)
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


_NATIVE_LANDLOCK_SOURCE = r'''
#include <Python.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <linux/seccomp.h>
#include <linux/filter.h>
#include <asm/unistd.h>
#include <linux/audit.h>
#include <stddef.h>
#include <unistd.h>
struct attr { uint64_t handled_access_fs, handled_access_net, scoped; };
struct beneath { uint64_t allowed_access; int32_t parent_fd; };
static PyObject *install(PyObject *self, PyObject *args) {
  unsigned long long handled; PyObject *paths;
  if (!PyArg_ParseTuple(args, "KO", &handled, &paths)) return NULL;
  struct attr attr = {(uint64_t)handled, 0, 0};
  int ruleset = (int)syscall(SYS_landlock_create_ruleset, &attr, 8, 0);
  if (ruleset < 0) return PyErr_SetFromErrno(PyExc_OSError);
  Py_ssize_t count = PySequence_Size(paths);
  for (Py_ssize_t i = 0; i < count; ++i) {
    PyObject *path = PySequence_GetItem(paths, i);
    const char *text = PyUnicode_AsUTF8(path);
    if (!text) { close(ruleset); return NULL; }
    int fd = open(text, O_PATH | O_CLOEXEC);

    Py_DECREF(path);
    if (fd < 0) { close(ruleset); return PyErr_SetFromErrno(PyExc_OSError); }
    struct beneath rule = {(uint64_t)handled, fd};
    int result = (int)syscall(SYS_landlock_add_rule, ruleset, 1, &rule, 0);

    int saved = errno; close(fd);
    if (result < 0) { close(ruleset); errno = saved; return PyErr_SetFromErrno(PyExc_OSError); }
  }
  if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) || syscall(SYS_landlock_restrict_self, ruleset, 0)) {
    int saved = errno; close(ruleset); errno = saved; return PyErr_SetFromErrno(PyExc_OSError);
  }
  close(ruleset); Py_RETURN_NONE;
}
static PyObject *seccomp_install(PyObject *self, PyObject *args) {
  PyObject *audit_object = PyTuple_GetItem(args, 0);
  if (audit_object == NULL || !PyLong_Check(audit_object)) return PyErr_Format(PyExc_TypeError, "audit must be an integer");
  unsigned long audit = PyLong_AsUnsignedLong(audit_object);
  if (PyErr_Occurred()) return NULL;
  int socket_nr = audit == AUDIT_ARCH_X86_64 ? 41 : 198;
  int socketpair_nr = audit == AUDIT_ARCH_X86_64 ? 53 : 199;
  int ptrace_nr = audit == AUDIT_ARCH_X86_64 ? 101 : 117;
  int bpf_nr = audit == AUDIT_ARCH_X86_64 ? 321 : 280;
  int readv_nr = audit == AUDIT_ARCH_X86_64 ? 310 : 270;
  int writev_nr = audit == AUDIT_ARCH_X86_64 ? 311 : 271;
  int kexec_nr = audit == AUDIT_ARCH_X86_64 ? 246 : 104;
  struct sock_filter filters[] = {
    BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, arch)),
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, audit, 0, 1),
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS),
    BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr)),
    BPF_JUMP(BPF_JMP | BPF_JSET | BPF_K, 0x40000000, 0, 1),
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS),
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, socket_nr, 6, 0),
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, socketpair_nr, 5, 0),
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, ptrace_nr, 4, 0),
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, bpf_nr, 3, 0),
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, readv_nr, 2, 0),
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, writev_nr, 1, 0),
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, kexec_nr, 0, 0),
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | EPERM),
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
  };
  struct sock_fprog program = {(unsigned short)(sizeof(filters) / sizeof(filters[0])), filters};
  if (syscall(__NR_seccomp, SECCOMP_SET_MODE_FILTER, 0UL, &program) < 0) return PyErr_SetFromErrno(PyExc_OSError);
  Py_RETURN_NONE;
}
static PyMethodDef methods[] = {{"install", install, METH_VARARGS}, {"seccomp_install", seccomp_install, METH_VARARGS}, {NULL, NULL, 0, NULL}};
static struct PyModuleDef module = {PyModuleDef_HEAD_INIT, "omnicrawler_landlock", NULL, -1, methods};
PyMODINIT_FUNC PyInit_omnicrawler_landlock(void) { return PyModule_Create(&module); }
'''


def _native_helper():
    """Build once before irreversible file confinement; ctypes is not ABI-safe here."""
    cached = globals().get("_native_module")
    if cached is not None:
        return cached
    import sysconfig
    directory = tempfile.mkdtemp(prefix="omnicrawler-landlock-")
    source = Path(directory) / "landlock.c"
    extension = Path(directory) / "omnicrawler_landlock.so"
    source.write_text(_NATIVE_LANDLOCK_SOURCE, encoding="utf-8")
    includes = sysconfig.get_paths()["include"]
    command = [os.environ.get("CC", "cc"), "-shared", "-fPIC", str(source), "-I", includes, "-o", str(extension)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(f"{E_UNSUPPORTED_ENV}: native Landlock shim compilation failed: {result.stderr[-500:]}")
    import importlib.util
    spec = importlib.util.spec_from_file_location("omnicrawler_landlock", extension)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{E_UNSUPPORTED_ENV}: native Landlock shim cannot load")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    globals()["_native_module"] = module
    return module


def _native_landlock(handled: int, paths: Sequence[Path]) -> None:
    _native_helper().install(handled, tuple(str(path) for path in paths))


def _landlock(access_fs: int, paths: Sequence[Path]) -> None:
    import ctypes

    if sys.platform != "linux":
        raise OSError(22, "Landlock is unavailable on this platform")
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long

    abi = _landlock_abi(libc)
    if abi < 1:
        raise OSError(ctypes.get_errno(), "Landlock ABI unavailable")
    handled = access_fs & _LANDLOCK_ACCESS_V1
    handled &= (1 << 13) - 1

    _native_landlock(handled, paths)


def _standard_library_roots() -> tuple[Path, ...]:
    roots = {Path(path).resolve() for path in sys.path if path and Path(path).is_dir()}
    roots.update(Path(value).resolve() for value in (sys.prefix, sys.base_prefix) if value)
    return tuple(sorted(roots, key=str))


def _linux_runtime_roots() -> tuple[Path, ...]:
    return tuple(
        Path(value) for value in (
            "/lib", "/lib64", "/usr/lib", "/usr/local/lib",
            "/etc/ssl", "/usr/share/zoneinfo",
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
    libc.syscall.restype = ctypes.c_long
    if _prctl(libc, _PR_SET_NO_NEW_PRIVS, 1):
        raise OSError(ctypes.get_errno(), "no_new_privs failed")
    audit = 0xC000003E if os.uname().machine == "x86_64" else 0xC00000B7
    _native_helper().seccomp_install(audit)
    return "landlock+seccomp"


def _quote(path: Path) -> str:
    return '"' + str(path.resolve()).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _macos_runtime_roots() -> tuple[Path, ...]:
    executable = Path(sys.executable).resolve()
    roots = [executable, executable.parent, sys.base_prefix and Path(sys.base_prefix).resolve()]
    for parent in executable.parents:
        if parent.name in ("Python", "hostedtoolcache"):
            roots.append(parent)
            if parent.name == "Python":
                roots.append(parent.parent)
    return tuple(path for path in roots if path)


def darwin_profile(*, plugin_root: Path, verified_entry_dir: Path | None = None) -> str:
    """Return a fail-closed Seatbelt profile with no direct network access."""
    roots = (*_standard_library_roots(), *_macos_runtime_roots(), plugin_root.resolve())
    if verified_entry_dir is not None:
        roots += (verified_entry_dir.resolve(),)
    allow = "\n".join(f"  (subpath {_quote(root)})" for root in roots)
    runtime = "\n".join(f"  (subpath {_quote(path)})" for path in (Path(sys.executable).resolve(), *Path(sys.executable).resolve().parents[:4]))
    return f"""(version 1)
(deny default)
(allow signal (target self))
(allow process-exec process-fork)
(allow mach-lookup)
(allow sysctl-read)
(allow file-read*
{allow}
)
(allow process-exec
{runtime}
  (literal "/usr/bin/sandbox-exec"))
(allow file-read*
  (subpath "/usr/bin")
  (subpath "/bin")
  (subpath "/System/Library")
  (subpath "/Library/Apple")
  (subpath "/Library/Frameworks/Python.framework")
  (subpath "/opt/hostedtoolcache")
  (subpath "/Library/Developer")
  (subpath "/System/Library")
  (subpath "/System/Volumes/Preboot")
  (subpath "/usr/local/share")
  (subpath "/usr/share")
  (subpath "/private/var/db/dyld")
  (subpath "/dev")
  (subpath "/private/etc")
  (subpath "/etc"))
(allow file-read-metadata)
(allow file-write* (literal "/dev/null"))
(deny network*)
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
