"""Unix enforcement semantics and real-kernel/Seatbelt smoke coverage."""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

import pytest

from omnicrawler.plugins import plugin_unix_enforcement as enforcement


def test_landlock_v1_uses_short_native_abi(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, tuple[Path, ...], tuple[Path, ...]]] = []
    class _Libc:
        syscall = type("Call", (), {})()
    monkeypatch.setattr(ctypes, "CDLL", lambda *args, **kwargs: _Libc())
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(enforcement, "_landlock_abi", lambda libc: 5)
    monkeypatch.setattr(
        enforcement, "_native_landlock",
        lambda handled, read_paths, write_paths: calls.append(
            (handled, tuple(read_paths), tuple(write_paths))
        ),
    )
    assert enforcement._LANDLOCK_ACCESS_V1 == 0x1FFF
    enforcement._landlock(
        enforcement._LANDLOCK_ACCESS_V1 | (1 << 13), (Path("plugin"),), (Path("scope"),)
    )
    assert calls == [(0x1FFF, (Path("plugin"),), (Path("scope"),))]


def test_landlock_grants_only_declared_temp_scope(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The generic host temp root is never recursively authorized for a plugin."""
    calls: list[tuple[tuple[Path, ...], tuple[Path, ...]]] = []
    scope = tmp_path / "plugin-scope"
    scope.mkdir()
    host_temp = tmp_path / "host-temp"
    host_temp.mkdir()
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        enforcement,
        "_standard_library_roots",
        lambda: (tmp_path / "stdlib",),
    )
    monkeypatch.setenv("OMNICRAWL_PLUGIN_RUNTIME_READ_ROOTS", str(host_temp))
    monkeypatch.setattr(enforcement, "_linux_runtime_roots", lambda: ())
    monkeypatch.setattr(
        enforcement,
        "_landlock",
        lambda access, read_paths, write_paths: calls.append(
            (tuple(read_paths), tuple(write_paths))
        ),
    )
    monkeypatch.setenv("OMNICRAWL_PLUGIN_RUNTIME_ROOTS", str(scope))
    assert enforcement.apply_linux(plugin_root=tmp_path / "plugin") == "landlock"
    read_paths, write_paths = calls[0]
    assert host_temp in read_paths
    assert scope not in read_paths
    assert write_paths == (scope,)


def test_darwin_profile_grants_only_declared_temp_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scope = tmp_path / "plugin-scope"
    scope.mkdir()
    host_temp = tmp_path / "host-temp"
    host_temp.mkdir()
    monkeypatch.setattr(enforcement, "_standard_library_roots", lambda: (host_temp,))
    monkeypatch.setattr(enforcement, "_macos_runtime_roots", lambda: ())
    monkeypatch.setattr(enforcement.sys, "executable", str(tmp_path / "python"))
    monkeypatch.setenv("OMNICRAWL_PLUGIN_RUNTIME_ROOTS", str(scope))
    profile = enforcement.darwin_profile(plugin_root=tmp_path / "plugin")
    assert f"(subpath {enforcement._quote(scope)})" in profile
    assert f"(subpath {enforcement._quote(host_temp)})" in profile
    assert '(allow file-read* file-write* (subpath "/private/var/folders"))' not in profile
    assert '(deny file-read*\n  (literal "/etc/passwd")' in profile


def test_native_landlock_requires_working_toolchain(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", "compiler unavailable"),
    )
    with pytest.raises(RuntimeError, match="native Landlock shim compilation failed"):
        enforcement._native_landlock(0x1FFF, (), ())


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
    calls: list[tuple[tuple[Path, ...], tuple[Path, ...]]] = []
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        enforcement,
        "_landlock",
        lambda access, read_paths, write_paths: calls.append(
            (tuple(read_paths), tuple(write_paths))
        ),
    )
    monkeypatch.setattr(
        enforcement, "_standard_library_roots", lambda: (tmp_path / "stdlib",)
    )
    monkeypatch.setattr(enforcement, "_linux_runtime_roots", lambda: ())
    root = tmp_path / "plugin"
    verified = tmp_path / "verified"
    assert enforcement.apply_linux(plugin_root=root, verified_entry_dir=verified) == "landlock"
    read_paths, _write_paths = calls[0]
    assert root in read_paths and verified in read_paths


def test_landlock_failures_are_not_silently_ignored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(enforcement, "_landlock", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("denied")))
    with pytest.raises(OSError, match="denied"):
        enforcement.apply_linux(plugin_root=tmp_path)


def test_darwin_launch_embeds_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(enforcement.shutil, "which", lambda name: "/usr/bin/sandbox-exec")
    root = tmp_path / "plugin"
    command = enforcement.darwin_launch(["host", "demo"], plugin_root=root)
    assert command[:2] == ["/usr/bin/sandbox-exec", "-p"]
    assert command[3:] == ["host", "demo"]
    profile = command[2]
    assert "(deny default)" in profile
    assert "(deny network*)" in profile
    assert '(literal "/")' not in profile
    assert '(subpath "/private/var/db/dyld")' not in profile
    assert '(subpath "/private/etc")' not in profile
    assert '(subpath "/private/etc/ssl")' not in profile
    assert '(subpath "/etc")' not in profile
    # Global read-metadata would let sandboxed code open arbitrary user files.
    assert "(allow file-read-metadata)" not in profile
    assert all(not line.strip().startswith("#") for line in profile.splitlines())
    assert f"(subpath {enforcement._quote(root)})" in profile


def test_darwin_launch_requires_seatbelt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(enforcement.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="E_UNSUPPORTED_ENV"):
        enforcement.darwin_launch(["host"], plugin_root=tmp_path)


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
        enforcement._landlock(0x1FFF, [], [])
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
        "try: open('/private/etc/passwd').read(); result['file']='allowed'",
        "except OSError: result['file']='denied'",
        "print(json.dumps(result))",
    ))
    command = enforcement.darwin_launch(
        [sys.executable, "-S", "-c", probe, str(tmp_path)], plugin_root=tmp_path
    )
    completed = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"import": 42, "network": "denied", "file": "denied"}
