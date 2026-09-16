"""W4.2：归档级便携冒烟工具（`tools/portable_archive_smoke.py`）的本地可验部分。

## 这里守的是什么

该工具的「下载」由 workflow 的 `actions/download-artifact` 负责（遵守"本机不下载便携包"的决策），
所以本地能验的是**真正容易写错、且写错了 CI 会以难懂方式失败**的那几段：

1. **平台归档的挑选**（选错 ⇒ 拿 Linux 的包去 Windows runner 上跑，报错信息还不指向原因）；
2. **解压**（zip / tar.gz 往返）；
3. **发行根定位** —— ★ 必须与 `portable_smoke_test._resolve_executable` **同源**：
   两处若各写一套候选列表，就会"工具说找到了、冒烟说没找到"，属于典型的判据分裂；
4. ★ **输出全 ASCII** —— 该 job 会在 `windows-latest` 上跑，cp1252 控制台遇到非 ASCII 会
   `UnicodeEncodeError`（这正是曾经咬掉 `quality` Windows job 的那个失败模式）。
   这里用 cp1252 包住 stdout 实跑一遍，把它变成**本地可验**。
"""

from __future__ import annotations

import importlib.util
import io
import tarfile
import zipfile
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[3] / "tools" / "portable_archive_smoke.py"
_TOOLS_DIR = _MODULE_PATH.parent


def _module():
    spec = importlib.util.spec_from_file_location("portable_archive_smoke", _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_zip(directory: Path, name: str, entries: dict[str, str]) -> Path:
    path = directory / name
    with zipfile.ZipFile(path, "w") as bundle:
        for relative, content in entries.items():
            bundle.writestr(relative, content)
    return path


def _make_targz(directory: Path, name: str, entries: dict[str, str]) -> Path:
    path = directory / name
    with tarfile.open(path, "w:gz") as bundle:
        for relative, content in entries.items():
            blob = content.encode("utf-8")
            info = tarfile.TarInfo(relative)
            info.size = len(blob)
            bundle.addfile(info, io.BytesIO(blob))
    return path


def test_find_archive_picks_the_platform_specific_archive(tmp_path: Path) -> None:
    module = _module()
    _make_zip(tmp_path, "OmniCrawler-0.13.0-Windows-Portable-Full.zip", {})
    _make_targz(tmp_path, "OmniCrawler-0.13.0-Linux-Portable-Full.tar.gz", {})
    (tmp_path / "OmniCrawler-0.13.0-Linux-Portable-Full.tar.xz").write_bytes(b"x")
    (tmp_path / "OmniCrawler-0.13.0-macOS-Portable-Full.dmg").write_bytes(b"x")

    assert module.find_archive(tmp_path, "windows").name.endswith(".zip")
    assert module.find_archive(tmp_path, "macos").name.endswith(".dmg")
    # Linux 先找 `.tar.xz`（正式容器），没有才退到 tar.gz
    assert module.find_archive(tmp_path, "linux").name.endswith(".tar.xz")


def test_find_archive_is_scoped_to_the_platform(tmp_path: Path) -> None:
    """只准备 Linux 归档时，其它平台必须**报错**而不是"随便挑一个"。

    ★ 这正是本地守卫实测抓到过的真实缺陷：宽松 glob `*Portable*.tar.gz` 会让
    **macOS 把 Linux 的 `...-Linux-Portable-*.tar.gz` 挑走**，而两者都能解压 ⇒
    CI 上表现为"macOS runner 冒烟了 Linux 包"，报错还不会指向"挑错了包"。
    """
    module = _module()
    _make_targz(tmp_path, "OmniCrawler-0.13.0-Linux-Portable-Full.tar.gz", {})
    for platform in ("windows", "macos"):
        with pytest.raises(FileNotFoundError):
            module.find_archive(tmp_path, platform)


def test_find_archive_error_message_lists_what_is_present(tmp_path: Path) -> None:
    """★ 报错必须**能定位原因**：把目录里实际有什么列出来（否则 CI 只给一句"没找到"）。"""
    module = _module()
    (tmp_path / "SHA256SUMS-linux.txt").write_text("", encoding="utf-8")
    with pytest.raises(FileNotFoundError) as info:
        module.find_archive(tmp_path, "linux")
    assert "SHA256SUMS-linux.txt" in str(info.value)


def test_extract_zip_and_targz_round_trip(tmp_path: Path) -> None:
    module = _module()
    zip_path = _make_zip(
        tmp_path, "bundle.zip", {"OmniCrawler/omnicrawler-cli.exe": "exe", "OmniCrawler/README": "hi"}
    )
    tar_path = _make_targz(tmp_path, "bundle.tar.gz", {"OmniCrawler/omnicrawler": "elf"})

    zip_out = tmp_path / "out-zip"
    tar_out = tmp_path / "out-tar"
    module.extract(zip_path, zip_out)
    module.extract(tar_path, tar_out)

    assert (zip_out / "OmniCrawler" / "omnicrawler-cli.exe").read_text(encoding="utf-8") == "exe"
    assert (tar_out / "OmniCrawler" / "omnicrawler").read_text(encoding="utf-8") == "elf"


def test_extract_rejects_an_unknown_container(tmp_path: Path) -> None:
    module = _module()
    bogus = tmp_path / "OmniCrawler-0.13.0-Linux-Portable-Full.7z"
    bogus.write_bytes(b"x")
    with pytest.raises(ValueError):
        module.extract(bogus, tmp_path / "out")


def test_locate_release_root_finds_the_directory_holding_the_entry_point(tmp_path: Path) -> None:
    module = _module()
    root = tmp_path / "OmniCrawler"
    root.mkdir()
    (root / "omnicrawler").write_text("", encoding="utf-8")
    (root / "readme.txt").write_text("", encoding="utf-8")
    assert module.locate_release_root(tmp_path) == root


def test_locate_release_root_agrees_with_the_smoke_test_resolver(tmp_path: Path) -> None:
    """★ 判据不得分裂：定位器与 `portable_smoke_test._resolve_executable` 必须同源。

    这里用 **macOS 布局**（`OmniCrawler.app/Contents/MacOS/omnicrawler-cli`）验证 ——
    若有人在本工具里另写一套候选列表，这条会红。
    """
    module = _module()
    macos_root = tmp_path / "OmniCrawler"
    entry = macos_root / "OmniCrawler.app" / "Contents" / "MacOS" / "omnicrawler-cli"
    entry.parent.mkdir(parents=True)
    entry.write_text("", encoding="utf-8")

    resolved = module.locate_release_root(tmp_path)
    assert resolved == macos_root
    smoke = module._load_smoke_module()  # noqa: SLF001 - 同源判据
    assert smoke._resolve_executable(resolved) == entry  # noqa: SLF001


def test_locate_release_root_fails_loudly_when_absent(tmp_path: Path) -> None:
    module = _module()
    (tmp_path / "OmniCrawler").mkdir()
    (tmp_path / "OmniCrawler" / "readme.txt").write_text("", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        module.locate_release_root(tmp_path)


def test_prints_survive_a_cp1252_console(tmp_path: Path, capsys) -> None:
    """★ 输出必须**全 ASCII**：该 job 会在 Windows runner（cp1252）上跑。

    做法与 `test_coverage_gates.py::test_prints_survive_a_cp1252_console` 同源 ——
    把 stdout 换成 cp1252 包装流实跑一遍，任何非 ASCII 字符都会当场 `UnicodeEncodeError`。
    这里走的路径天然会失败（zip 里没有入口点）⇒ 顺带验证"失败也是 ASCII 报错"。
    """
    module = _module()
    archive_dir = tmp_path / "downloaded"
    archive_dir.mkdir()
    _make_zip(archive_dir, "OmniCrawler-0.13.0-Linux-Portable-Full.zip", {"README": "x"})

    buffer = io.BytesIO()
    console = io.TextIOWrapper(buffer, encoding="cp1252")
    original = module.sys.stdout
    module.sys.stdout = console
    try:
        with pytest.raises(FileNotFoundError):
            module.run(archive_dir, "linux", "Full")
    finally:
        module.sys.stdout = original
        console.flush()

    printed = buffer.getvalue()
    assert printed, "工具在 cp1252 控制台上什么都没打印 ⇒ 本用例在空转"
    assert printed.decode("ascii"), "工具打印了非 ASCII ⇒ 会在 Windows runner 上崩"
    assert b"portable archive:" in printed


def test_runs_as_a_script_with_a_bare_interpreter() -> None:
    """★ 该 job 里只有 `actions/setup-python` 的**裸解释器**（没有项目 venv）⇒
    工具必须只靠标准库就能导入并给出帮助。

    判据用**子进程实跑**（而不是"导入成功了"这种自证）：`--help` 退出码为 0 就证明
    导入链上没有第三方依赖、argparse 也可用。
    """
    import subprocess
    import sys

    completed = subprocess.run(  # noqa: S603 - argv 全为字面量 + sys.executable
        [sys.executable, str(_MODULE_PATH), "--help"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "--platform" in completed.stdout
    assert "--manifest" in completed.stdout
