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


def test_parent_compiled_helper_is_loaded_without_compiling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The confined child loads only the explicitly supplied parent-built shim."""
    helper_path = tmp_path / "omnicrawler_landlock.so"
    helper_path.write_bytes(b"compiled by the trusted parent")
    import importlib.util
    import subprocess
    from types import SimpleNamespace
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("compiled in child"))
    monkeypatch.setenv("OMNICRAWL_LANDLOCK_HELPER", str(helper_path))
    monkeypatch.setattr(enforcement, "_native_module", None, raising=False)
    loaded: list[Path] = []

    class Loader:
        path = str(helper_path)

        @staticmethod
        def create_module(spec: object) -> object:
            return SimpleNamespace(__file__=str(helper_path))

        @staticmethod
        def exec_module(module: object) -> None:
            loaded.append(Path(module.__file__))

    monkeypatch.setattr(
        importlib.util,
        "spec_from_file_location",
        lambda name, path: importlib.machinery.ModuleSpec(name, Loader()),
        raising=False,
    )
    assert Path(enforcement._native_helper().__file__) == helper_path
    assert loaded == [helper_path]


def test_linux_passes_verified_entry_and_runtime_roots(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(enforcement, "_landlock", lambda access, paths: calls.append(paths))
    monkeypatch.setattr(
        enforcement, "_standard_library_roots", lambda: (tmp_path / "stdlib",)
    )
    monkeypatch.setattr(enforcement, "_linux_runtime_roots", lambda: ())
    root = tmp_path / "plugin"
    verified = tmp_path / "verified"
    assert enforcement.apply_linux(plugin_root=root, verified_entry_dir=verified) == "landlock"
    assert root in calls[0] and verified in calls[0]


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
        assert '(allow file-read*\n  ; dyld' in profile
        assert '(literal "/")' in profile
        assert all(not line.strip().startswith("#") for line in profile.splitlines())
        assert f"(subpath {enforcement._quote(root)})" in profile
    assert not Path(command[2]).exists()


def test_darwin_launch_requires_seatbelt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(enforcement.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="E_UNSUPPORTED_ENV"):
        with enforcement.darwin_launch(["host"], plugin_root=tmp_path):
            pass


@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux Landlock ABI")
def test_landlock_abi_below_4_refuses_to_load() -> None:
    """Landlock network rules need ABI >= 4; older kernels must fail closed."""
    import ctypes

    class _Libc:
        @staticmethod
        def syscall(*args: object) -> int:
            return 3  # ABI 3 — no network handled support

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(ctypes, "CDLL", lambda *a, **k: _Libc())
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(OSError, match="ABI >= 4"):
        enforcement._landlock(0x1FFF, [])
    monkeypatch.undo()


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
    s = socket.socket()
    s.settimeout(2)
    s.connect(('127.0.0.1', 1))
    s.close()
    result['network'] = 'allowed'
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
    probe = "import json,socket,sys; sys.path.insert(0,sys.argv[1]); import probe; result={'import': probe.value}\n"
    probe += "\n".join((
        "try: s=socket.socket(); s.settimeout(2); s.connect(('127.0.0.1',1)); s.close(); result['network']='allowed'",
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
