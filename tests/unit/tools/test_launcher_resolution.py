"""版本无关启动器（Linux/macOS 的 `--to-versions` 消费者）的**行为**判据。

背景：`--to-versions` 把新版装到 `<安装根>/versions/<版本>/`，靠 `versions/current.txt`
决定当前生效的是哪一份。此前 Linux 上没有任何东西读那个指针（入口是安装期烘死的绝对路径，
都指向就地那份）⇒ 装了新版没人启动它，所以 `--to-versions` 在非 Windows 上是**被拒绝**的。
现在随包发两个启动器（GUI / CLI），本文件钉住它们的解析行为。

★ 这些用例在 Windows 上跳过（没有 `/bin/sh`），但**不能因此完全不验证**：
  判据已在开发机上用 Git Bash 的 `sh` 逐条跑过（指针命中 / CRLF 指针 / 目标不存在 /
  指针为空 / 无指针 / CLI 两种命名布局 / 缺二进制），本文件把同样的场景固化成 CI 用例
  （ubuntu 与 macOS 腿会执行）。

★★ 每个用例**只创建它需要的那一个二进制**：`OmniCrawler`（GUI）与 `omnicrawler`（CLI）
  只差大小写，而 **macOS 默认卷大小写不敏感** —— 两个都建会互相覆盖，
  测试就会变成"看起来通过了、其实测的是另一个文件"。
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DELIVERY_DIR = REPO_ROOT / "packaging" / "linux"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="启动器是 POSIX sh 脚本，Windows 上无法直接执行"
)

LAUNCHERS = ("OmniCrawler-launcher", "omnicrawler-cli-launcher")
VERSION = "1.2.3"


def _stub(path: Path, marker: str) -> None:
    """造一个假二进制：回显 marker 与安装根环境变量（用来证明 exec 到了谁）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\n"
        f'echo "{marker}"\n'
        'echo "INSTALL_ROOT=${OMNICRAWL_INSTALL_ROOT:-<unset>}"\n'
        'echo "ARGS=$*"\n',
        encoding="utf-8",
        newline="\n",
    )
    path.chmod(0o755)


def _root(tmp_path: Path, binaries: dict[str, str], *, pointer: str | None) -> Path:
    """建一棵安装树：``binaries`` 是 ``相对路径 -> marker``（只建给定的那些）。"""
    root = tmp_path / "install"
    root.mkdir(parents=True, exist_ok=True)
    for relative, marker in binaries.items():
        _stub(root / relative, marker)
    for name in LAUNCHERS:
        target = root / name
        shutil.copy2(DELIVERY_DIR / name, target)
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    if pointer is not None:
        (root / "versions").mkdir(parents=True, exist_ok=True)
        (root / "versions" / "current.txt").write_text(pointer, encoding="utf-8", newline="")
    return root


def _run(root: Path, launcher: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(root / launcher), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        # 去掉可能从父进程继承来的安装根，验证启动器**自己**设置它
        env={k: v for k, v in os.environ.items() if k != "OMNICRAWL_INSTALL_ROOT"},
    )


def _gui_root(tmp_path: Path, pointer: str | None) -> Path:
    return _root(
        tmp_path,
        {"OmniCrawler": "IN-PLACE-GUI", f"versions/{VERSION}/OmniCrawler": "VERSIONED-GUI"},
        pointer=pointer,
    )


def test_launcher_starts_the_pointed_version(tmp_path: Path) -> None:
    """有指针且指向的目录完整 ⇒ 启动**那一份**（而不是就地那份）。"""
    root = _gui_root(tmp_path, f"{VERSION}\n")

    result = _run(root, "OmniCrawler-launcher", "hello")

    assert result.returncode == 0, result.stderr
    assert "VERSIONED-GUI" in result.stdout
    assert "IN-PLACE-GUI" not in result.stdout
    assert "ARGS=hello" in result.stdout


def test_launcher_sets_install_root_for_the_child(tmp_path: Path) -> None:
    """★ 启动器必须把 `OMNICRAWL_INSTALL_ROOT` 交给子进程。

    这是"数据根钉在安装根"的唯一来源：没有它，应用会按"运行目录"推数据根 ⇒
    `versions/<版本>/` 里长出自己的 `work/`、`data/`，而清理旧版本时会被一起删掉。
    """
    root = _gui_root(tmp_path, f"{VERSION}\n")

    result = _run(root, "OmniCrawler-launcher")

    assert f"INSTALL_ROOT={root}" in result.stdout, result.stdout


@pytest.mark.parametrize(
    ("pointer", "why"),
    [
        ("\n", "指针为空"),
        ("9.9.9\n", "指针指向不存在的版本目录"),
    ],
)
def test_launcher_falls_back_when_pointer_is_unusable(
    tmp_path: Path, pointer: str, why: str
) -> None:
    """指针不可用 ⇒ 回退就地那份（**绝不能**让人打不开应用）。"""
    root = _gui_root(tmp_path, pointer)

    result = _run(root, "OmniCrawler-launcher")

    assert result.returncode == 0, f"{why}: {result.stderr}"
    assert "IN-PLACE-GUI" in result.stdout


def test_launcher_falls_back_without_pointer_file(tmp_path: Path) -> None:
    """没有指针文件（就地布局）⇒ 启动就地那份。"""
    root = _gui_root(tmp_path, None)

    result = _run(root, "OmniCrawler-launcher")

    assert result.returncode == 0, result.stderr
    assert "IN-PLACE-GUI" in result.stdout


def test_launcher_falls_back_when_pointer_target_is_not_executable(tmp_path: Path) -> None:
    """★ 目标存在但**不可执行**（半途中断/权限错了）⇒ 也回退。

    判据是 `-x` 而不是 `-f`：一个不完整的版本目录不该被当成"可用"。
    """
    root = _gui_root(tmp_path, f"{VERSION}\n")
    (root / "versions" / VERSION / "OmniCrawler").chmod(0o644)

    result = _run(root, "OmniCrawler-launcher")

    assert result.returncode == 0, result.stderr
    assert "IN-PLACE-GUI" in result.stdout


def test_launcher_tolerates_crlf_pointer(tmp_path: Path) -> None:
    """指针被 Windows 侧写过（CRLF）⇒ 也要能正确解析（否则路径里会多一个 CR）。"""
    root = _gui_root(tmp_path, f"{VERSION}\r\n")

    result = _run(root, "OmniCrawler-launcher")

    assert "VERSIONED-GUI" in result.stdout, result.stdout


def test_cli_launcher_resolves_linux_layout_name(tmp_path: Path) -> None:
    """CLI 启动器在 Linux 布局下找 `omnicrawler`，且不会误启动 GUI。"""
    root = _root(
        tmp_path,
        {"omnicrawler": "IN-PLACE-CLI", f"versions/{VERSION}/omnicrawler": "VERSIONED-CLI"},
        pointer=f"{VERSION}\n",
    )

    result = _run(root, "omnicrawler-cli-launcher", "--version")

    assert result.returncode == 0, result.stderr
    assert "VERSIONED-CLI" in result.stdout
    assert "ARGS=--version" in result.stdout


def test_cli_launcher_resolves_macos_layout_name(tmp_path: Path) -> None:
    """★ CLI 启动器在 **macOS 布局**下找 `omnicrawler-cli`。

    为什么需要两个候选名：macOS 默认卷**大小写不敏感**，`OmniCrawler` 与 `omnicrawler`
    会撞车 ⇒ macOS 包的 CLI 叫 `omnicrawler-cli`（与 `bundled_cli_path()` 同一套约定）。
    只认一个名字的话，同一份启动器会在某个平台上"找不到入口"。
    """
    root = _root(
        tmp_path,
        {"omnicrawler-cli": "IN-PLACE-CLI", f"versions/{VERSION}/omnicrawler-cli": "VERSIONED-CLI"},
        pointer=f"{VERSION}\n",
    )

    result = _run(root, "omnicrawler-cli-launcher", "--version")

    assert result.returncode == 0, result.stderr
    assert "VERSIONED-CLI" in result.stdout


def test_launcher_reports_clearly_when_nothing_is_runnable(tmp_path: Path) -> None:
    """★ 就地那份与版本那份都不在 ⇒ **明确报错**（而不是 exec 失败留下一句天书）。"""
    root = _root(tmp_path, {}, pointer=None)

    result = _run(root, "OmniCrawler-launcher")

    assert result.returncode != 0
    assert "未找到可执行文件" in result.stderr
