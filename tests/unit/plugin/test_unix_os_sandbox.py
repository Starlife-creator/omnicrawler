"""Unix enforcement semantics and real-kernel/Seatbelt smoke coverage."""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

import pytest

from omnicrawler.plugins import plugin_unix_enforcement as enforcement


def test_landlock_v1_uses_short_native_abi(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, tuple[Path, ...]]] = []
    class _Libc:
        syscall = type("Call", (), {})()
    monkeypatch.setattr(ctypes, "CDLL", lambda *args, **kwargs: _Libc())
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(enforcement, "_landlock_abi", lambda libc: 5)
    monkeypatch.setattr(
        enforcement, "_native_landlock",
        lambda handled, paths: calls.append((handled, tuple(paths))),
    )
    assert enforcement._LANDLOCK_ACCESS_V1 == 0x1FFF
    enforcement._landlock(enforcement._LANDLOCK_ACCESS_V1 | (1 << 13), (Path("plugin"),))
    assert calls == [(0x1FFF, (Path("plugin"),))]


def test_native_landlock_requires_working_toolchain(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", "compiler unavailable"),
    )
    with pytest.raises(RuntimeError, match="native Landlock shim compilation failed"):
        enforcement._native_landlock(0x1FFF, ())


def test_linux_capability_checks_landlock_and_seccomp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    class _Libc:
        def __init__(self) -> None:
            self.syscall = self
            self.argtypes: list[object] = []

        def __call__(self, *args: object) -> int:
            return 0

    monkeypatch.setattr(ctypes, "CDLL", lambda *args, **kwargs: _Libc())
    monkeypatch.setattr(os, "uname", lambda: os.uname_result(("Linux", "host", "6", "v", "x86_64")), raising=False)
    monkeypatch.setattr(enforcement, "_landlock_abi", lambda libc: 3)
    monkeypatch.setattr(enforcement, "_prctl", lambda libc, option, arg2=0: 2)
    assert enforcement.linux_capability()
    monkeypatch.setattr(enforcement, "_prctl", lambda libc, option, arg2=0: -1)
    assert not enforcement.linux_capability()


def test_linux_does_not_install_on_other_platforms(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    with pytest.raises(RuntimeError, match="E_UNSUPPORTED_ENV"):
        enforcement.apply_linux(plugin_root=Path("."))


def test_linux_passes_verified_entry_and_runtime_roots(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(enforcement, "_landlock", lambda access, paths: calls.append(paths))
    program = ctypes.c_int()
    monkeypatch.setattr(enforcement, "_sock_filter", lambda: (b"array", program))
    monkeypatch.setattr(os, "uname", lambda: os.uname_result(("Linux", "host", "6", "v", "x86_64")), raising=False)
    monkeypatch.setattr(
        enforcement, "_standard_library_roots", lambda: (tmp_path / "stdlib",)
    )
    monkeypatch.setattr(enforcement, "_linux_runtime_roots", lambda: ())

    class _Native:
        @staticmethod
        def seccomp_install(audit: int) -> None:
            calls.append(audit)

    monkeypatch.setattr(enforcement, "_native_helper", lambda: _Native())

    class _Libc:
        @staticmethod
        def syscall(*args: object) -> int:
            calls.append(args)
            return 0

    monkeypatch.setattr(ctypes, "CDLL", lambda *args, **kwargs: _Libc())
    root = tmp_path / "plugin"
    verified = tmp_path / "verified"
    assert enforcement.apply_linux(plugin_root=root, verified_entry_dir=verified) == "landlock+seccomp"
    assert root in calls[0] and verified in calls[0]
    assert any(len(call) > 3 and call[1] == enforcement._PR_SET_NO_NEW_PRIVS for call in calls[1:] if isinstance(call, tuple))
    assert calls[-1] == 0xC000003E


def test_landlock_failures_are_not_silently_ignored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(enforcement, "_landlock", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("denied")))
    with pytest.raises(OSError, match="denied"):
        enforcement.apply_linux(plugin_root=tmp_path)


def test_darwin_launch_wraps_command_and_deletes_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(enforcement.shutil, "which", lambda name: "/usr/bin/sandbox-exec")
    root = tmp_path / "plugin"
    with enforcement.darwin_launch(["host", "demo"], plugin_root=root) as command:
        assert command[:3] == ["/usr/bin/sandbox-exec", "-f", command[2]]
        assert command[3:] == ["host", "demo"]
        profile = Path(command[2]).read_text(encoding="utf-8")
        assert "(deny default)" in profile
        assert "(deny network*)" in profile
        assert "(allow file-read*" in profile
        assert f"(subpath {enforcement._quote(root)})" in profile
    assert not Path(command[2]).exists()


def test_darwin_launch_requires_seatbelt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(enforcement.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="E_UNSUPPORTED_ENV"):
        with enforcement.darwin_launch(["host"], plugin_root=tmp_path):
            pass


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux syscall numbers")
def test_seccomp_does_not_allow_nonmatching_syscalls() -> None:
    """Evaluate the exact BPF control flow, including x32 and wrong ABIs."""
    array, _program = enforcement._sock_filter()
    print("ARRAY_FIELDS", [(x.code, x.jt, x.jf, x.k) for x in array], flush=True)
    audit = 0xC000003E if os.uname().machine == "x86_64" else 0xC00000B7

    def evaluate(number: int, arch: int) -> int:
        accumulator = 0
        pc = 0
        while pc < len(array):
            instruction = array[pc]
            if instruction.code & 0x07 == enforcement._BPF_LD:
                accumulator = number if instruction.k == 0 else arch
            elif instruction.code & 0x07 == enforcement._BPF_JMP:
                operation = instruction.code & 0xF0
                print("EVAL", accumulator, instruction.k, operation, accumulator == instruction.k, flush=True)
                matched = ((accumulator & instruction.k) != 0) if operation == enforcement._BPF_JSET else accumulator == instruction.k
                assert operation in (enforcement._BPF_JEQ, enforcement._BPF_JSET)
                assert instruction.code & 0x07 == enforcement._BPF_JMP
                pc = pc + 1 + (instruction.jt if matched else instruction.jf)
            elif instruction.code & 0x07 == enforcement._BPF_RET:
                return instruction.k
            else:
                pytest.fail("unknown BPF operation")
            if instruction.code & 0x07 != enforcement._BPF_JMP:
                pc += 1
        pytest.fail("BPF program has no return")

    socket = enforcement._syscall_number("socket")
    assert evaluate(0, audit) == enforcement._RET_ALLOW
    assert evaluate(socket, audit) == enforcement._RET_ERRNO
    assert evaluate(0, 0) == enforcement._RET_KILL
    assert evaluate(0x40000000, audit) == enforcement._RET_KILL
    assert evaluate(0x40000000 | socket, audit) == enforcement._RET_KILL


@pytest.mark.skipif(sys.platform != "linux", reason="requires the real Linux kernel")
def test_linux_enforcement_allows_imports_and_blocks_escape(tmp_path: Path) -> None:
    """Run in a child: the filter is intentionally irreversible."""
    import json
    import subprocess

    plugin = tmp_path / "probe.py"
    plugin.write_text("def handle(operation, payload):\n    return {'ok': True}\n", encoding="utf-8")
    probe = r"""
import json, os, socket, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[2])))
sys.path.insert(0, str(Path(sys.argv[1])))
from plugin_unix_enforcement import apply_linux
from pathlib import Path
apply_linux(plugin_root=Path(sys.argv[1]))
import probe
result = {}
try:
    socket.socket()
except OSError:
    result['network'] = 'denied'
try:
    Path('/etc/passwd').read_text()
    result['file'] = 'allowed'
except OSError:
    result['file'] = 'denied'
result['import'] = probe.handle('', {})['ok']
print(json.dumps(result))
"""
    source = Path(enforcement.__file__).resolve().parent
    completed = subprocess.run(
        [sys.executable, "-I", "-S", "-c", probe, str(tmp_path), str(source)],
        capture_output=True, text=True, timeout=15,
        env={key: value for key, value in {**os.environ, "PYTHONPATH": str(source)}.items()
             if key != "PYTHONHOME"}, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"network": "denied", "file": "denied", "import": True}


@pytest.mark.skipif(sys.platform != "darwin", reason="requires real macOS Seatbelt")
def test_seatbelt_allows_plugin_read_and_blocks_escape(tmp_path: Path) -> None:
    import json
    import subprocess

    plugin = tmp_path / "probe.py"
    plugin.write_text("value = 42\n", encoding="utf-8")
    probe = "import json,socket,sys; sys.path.insert(0,sys.argv[1]); import probe; result={'import': probe.value}; "
    probe += "\n".join((
        "try: socket.socket(); result['network']='allowed'",
        "except OSError: result['network']='denied'",
        "try: open('/etc/passwd').read(); result['file']='allowed'",
        "except OSError: result['file']='denied'",
        "print(json.dumps(result))",
    ))
    with enforcement.darwin_launch(
        [sys.executable, "-S", "-c", probe, str(tmp_path)], plugin_root=tmp_path
    ) as command:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"import": 42, "network": "denied", "file": "denied"}
