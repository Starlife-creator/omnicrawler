"""I1a / I1b：Linux 用户级安装交付件的守卫（判据工具 + 脚本真实行为）。

分两组，理由不同
----------------

**A 组（平台无关，任何平台都跑）**：`tools/check_linux_delivery.py` 是安装交付件的
**唯一判据**（LF / 无 BOM / 脚本头 / `.desktop` 契约字段 / hicolor 图标源齐备）。
这里把每种缺陷**装回去**，断言它立刻判红，再**字节级还原**并比对 —— 否则
"判据看起来在跑" 与 "判据真的会说话" 分不开。

**B 组（POSIX only）**：真的把 `install-user.sh` / `uninstall-user.sh` 跑起来，
在 `tmp_path` 下伪造应用树 + 临时 `HOME`，断言落位与**卸载的数据保护**。
★ 卸载最关键的一条是**反向断言**：默认卸载后应用树必须**仍在**（便携模式下数据
就在应用树内，删树 = 删用户数据）；带 `PORTABLE.flag` 时 `--remove-prefix` 必须
**拒绝**（非零退出）而不是"客气地删掉"。

为什么 B 组只在 POSIX 上跑（不是偷懒，是判据得站在**被度量的环境**里）：

* 交付脚本面向 Linux 用户环境 —— 它只进 Linux 便携包、只由 Linux 构建脚本装配；
* 非 POSIX 的 bash（Git Bash）在**两处语义不同**：`ln -s` 不建真符号链接
  （实测退化成 0 字节普通文件），且有自己的挂载表（`C:/…` 与 `%TEMP%` 会映射成
  `/c/…` 与 `/tmp/…`），于是"`Exec=` 里的绝对路径"在 Windows 侧是**另一个字符串**。
  在这种环境里断言，得到的失败与被测行为无关 —— 属于红线里的"判据位置不对"。
* 想在 Windows 上复核，用同一套手工沙盒即可：造一棵
  `app/{OmniCrawler,omnicrawler,installer/…,PORTABLE.flag}`、设 `HOME` 到临时目录、
  `bash app/installer/install-user.sh --prefix <tmp>/prefix --no-desktop-database`，
  再跑 `uninstall-user.sh`。**本批就是靠它抓到**了"prefix 归一化落空 ⇒ 把整棵树
  写到 `/prefix`"这个真缺陷（现已改为显式报错）。
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DELIVERY_DIR = REPO_ROOT / "packaging" / "linux"
BRANDING_SRC = REPO_ROOT / "src" / "omnicrawler" / "gui" / "branding"

# `tools/` 不是包，按路径加载那个唯一判据（构建期调的是同一个文件）。
_spec = importlib.util.spec_from_file_location(
    "_check_linux_delivery", REPO_ROOT / "tools" / "check_linux_delivery.py"
)
assert _spec is not None and _spec.loader is not None
check_linux_delivery = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_linux_delivery)

# I1b 第②层（归档级安装冒烟）也要能被单测直接驱动，才能做反向断言。
_smoke_spec = importlib.util.spec_from_file_location(
    "_portable_archive_smoke", REPO_ROOT / "tools" / "portable_archive_smoke.py"
)
assert _smoke_spec is not None and _smoke_spec.loader is not None
archive_smoke = importlib.util.module_from_spec(_smoke_spec)
_smoke_spec.loader.exec_module(archive_smoke)

DELIVERY_FILES = ("install-user.sh", "uninstall-user.sh", "omnicrawler.desktop.in")
HICOLOR_SIZES = (16, 24, 32, 48, 64, 128, 256)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stage_delivery(tmp_path: Path) -> Path:
    """把真实交付件拷进 tmp（保持字节），供"装回缺陷"使用。"""
    staged = tmp_path / "delivery"
    staged.mkdir()
    for name in DELIVERY_FILES:
        shutil.copy2(DELIVERY_DIR / name, staged / name)
    return staged


def _stage_branding(tmp_path: Path) -> Path:
    branding = tmp_path / "branding"
    branding.mkdir()
    for size in HICOLOR_SIZES:
        shutil.copy2(
            BRANDING_SRC / f"omnicrawler-icon-{size}.png",
            branding / f"omnicrawler-icon-{size}.png",
        )
    return branding


# ── A 组：判据本身 ────────────────────────────────────────────────────────


def test_delivered_files_pass_the_check() -> None:
    """正向：仓库里的真实交付件必须通过（否则下面所有反向断言都无意义）。"""
    errors = check_linux_delivery.check(DELIVERY_DIR, BRANDING_SRC)
    assert errors == [], errors


def test_checker_goes_red_on_crlf_and_restores_byte_identically(tmp_path: Path) -> None:
    """★ 反向断言：LF 换成 CRLF ⇒ 必须红 ⇒ 字节级还原后 sha256 一致。"""
    staged = _stage_delivery(tmp_path)
    hook = staged / "install-user.sh"
    pristine = hook.read_bytes()
    pristine_sha = _sha256(hook)

    hook.write_bytes(pristine.replace(b"\n", b"\r\n"))
    errors = check_linux_delivery.check(staged)
    assert any("contains CR" in err for err in errors), errors

    hook.write_bytes(pristine)
    assert _sha256(hook) == pristine_sha
    assert check_linux_delivery.check(staged) == []


def test_checker_goes_red_on_utf8_bom(tmp_path: Path) -> None:
    staged = _stage_delivery(tmp_path)
    target = staged / "uninstall-user.sh"
    pristine = target.read_bytes()
    target.write_bytes(b"\xef\xbb\xbf" + pristine)
    errors = check_linux_delivery.check(staged)
    assert any("BOM" in err for err in errors), errors
    target.write_bytes(pristine)
    assert check_linux_delivery.check(staged) == []


def test_checker_goes_red_when_a_script_header_is_lost(tmp_path: Path) -> None:
    """shebang / `set -euo pipefail` 掉了也必须是红的（否则静默半坏）。"""
    staged = _stage_delivery(tmp_path)
    target = staged / "install-user.sh"
    pristine = target.read_bytes()
    target.write_bytes(pristine.replace(b"#!/usr/bin/env bash\n", b"", 1))
    errors = check_linux_delivery.check(staged)
    assert any("must start with" in err for err in errors), errors
    target.write_bytes(pristine)
    assert check_linux_delivery.check(staged) == []


def test_checker_goes_red_when_desktop_contract_line_is_dropped(tmp_path: Path) -> None:
    """`.desktop` 的跨文件契约字段被删（Exec/Icon/StartupWMClass）必须红。"""
    staged = _stage_delivery(tmp_path)
    template = staged / "omnicrawler.desktop.in"
    pristine = template.read_bytes()
    template.write_bytes(pristine.replace(b"Icon=omnicrawler\n", b"", 1))
    errors = check_linux_delivery.check(staged)
    assert any("Icon=omnicrawler" in err for err in errors), errors
    template.write_bytes(pristine)
    assert check_linux_delivery.check(staged) == []


def test_checker_goes_red_when_a_hicolor_source_is_missing(tmp_path: Path) -> None:
    """I1b：7 个尺寸缺一 ⇒ 红（否则桌面条目图标静默缺尺寸）。"""
    staged = _stage_delivery(tmp_path)
    branding = _stage_branding(tmp_path)
    (branding / "omnicrawler-icon-48.png").unlink()
    errors = check_linux_delivery.check(staged, branding)
    assert any("omnicrawler-icon-48.png" in err for err in errors), errors


# ── B 组：脚本真实行为（POSIX only，见模块 docstring） ─────────────────────

BASH = shutil.which("bash")
needs_posix_bash = pytest.mark.skipif(
    os.name != "posix" or BASH is None,
    reason=(
        "安装脚本面向 Linux 用户环境，只在 POSIX 上断言：非 POSIX 的 bash "
        "（Git Bash）不建真符号链接，且挂载表会把绝对路径映射成另一个字符串"
    ),
)


def _make_app_tree(tmp_path: Path) -> Path:
    """伪造一棵与产物同形的应用树：入口 + installer/{脚本,模板,hicolor}。"""
    app = tmp_path / "download" / "OmniCrawler"
    (app / "installer" / "icons" / "hicolor").mkdir(parents=True)
    for name in DELIVERY_FILES:
        shutil.copy2(DELIVERY_DIR / name, app / "installer" / name)
    for size in HICOLOR_SIZES:
        dest = app / "installer" / "icons" / "hicolor" / f"{size}x{size}" / "apps"
        dest.mkdir(parents=True)
        shutil.copy2(BRANDING_SRC / f"omnicrawler-icon-{size}.png", dest / "omnicrawler.png")
    for entry in ("OmniCrawler", "omnicrawler"):
        stub = app / entry
        stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)
    # 便携标记 + 用户数据：卸载的数据保护就是冲它来的
    (app / "PORTABLE.flag").write_text("", encoding="utf-8")
    (app / "output").mkdir()
    (app / "output" / "precious.json").write_text("{}", encoding="utf-8")
    return app


def _run(script: Path, args: list[str], home: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["HOME"] = str(home)
    env.pop("XDG_DATA_HOME", None)
    return subprocess.run(  # noqa: S603 - argv 全部由本用例构造，无外部输入
        [str(BASH), str(script), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )


@needs_posix_bash
def test_install_places_everything_and_is_idempotent(tmp_path: Path) -> None:
    app = _make_app_tree(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    prefix = tmp_path / "prefix"

    first = _run(app / "installer" / "install-user.sh",
                 ["--prefix", str(prefix), "--no-desktop-database"], home)
    assert first.returncode == 0, first.stdout + first.stderr

    desktop = home / ".local" / "share" / "applications" / "omnicrawler.desktop"
    assert desktop.is_file()
    text = desktop.read_text(encoding="utf-8")
    exec_line = next(line for line in text.splitlines() if line.startswith("Exec="))
    exec_target = Path(exec_line.removeprefix("Exec="))
    # ★ 必须是**绝对**路径，且指向 prefix 下真实存在的入口（就地注册会留死链）
    assert exec_target.is_absolute(), exec_line
    assert exec_target == prefix / "OmniCrawler", exec_line
    assert exec_target.is_file(), "Exec= 指向的入口在磁盘上必须真实存在"
    assert "Icon=omnicrawler" in text
    assert "StartupWMClass=omnicrawler" in text

    for size in HICOLOR_SIZES:
        icon = (home / ".local" / "share" / "icons" / "hicolor"
                / f"{size}x{size}" / "apps" / "omnicrawler.png")
        assert icon.is_file(), icon

    assert (prefix / "OmniCrawler").is_file()
    assert (home / ".local" / "bin" / "omnicrawler").exists()

    # 幂等：重跑不报错、图标不叠加
    second = _run(app / "installer" / "install-user.sh",
                  ["--prefix", str(prefix), "--no-desktop-database"], home)
    assert second.returncode == 0, second.stdout + second.stderr
    installed = list((home / ".local" / "share" / "icons").rglob("omnicrawler.png"))
    assert len(installed) == len(HICOLOR_SIZES)


@needs_posix_bash
def test_install_explains_the_deps_it_cannot_install(tmp_path: Path) -> None:
    """前置检查只**报告**、不自动 apt：缺依赖时给出可复制的安装命令。"""
    app = _make_app_tree(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    prefix = tmp_path / "prefix"
    # 用一个不含任何 Qt 库的假 ldconfig，确保走"缺依赖"分支
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    fake_ldconfig = fake_bin / "ldconfig"
    fake_ldconfig.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_ldconfig.chmod(0o755)

    env_home = home
    result = subprocess.run(  # noqa: S603 - argv 由本用例构造
        [str(BASH), str(app / "installer" / "install-user.sh"),
         "--prefix", str(prefix), "--no-desktop-database"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "HOME": str(env_home), "PATH": f"{fake_bin}:{os.environ['PATH']}"},
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert "apt-get install" in combined, combined
    assert "libegl1" in combined, combined


@needs_posix_bash
def test_uninstall_keeps_the_app_tree_by_default(tmp_path: Path) -> None:
    """★ 反向断言：默认卸载必须**不删应用树**（便携模式下那里有用户数据）。"""
    app = _make_app_tree(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    prefix = tmp_path / "prefix"
    assert _run(app / "installer" / "install-user.sh",
                ["--prefix", str(prefix), "--no-desktop-database"], home).returncode == 0

    removed = _run(prefix / "installer" / "uninstall-user.sh", ["--prefix", str(prefix)], home)
    assert removed.returncode == 0, removed.stdout + removed.stderr

    assert not (home / ".local" / "share" / "applications" / "omnicrawler.desktop").exists()
    assert list((home / ".local" / "share" / "icons").rglob("omnicrawler.png")) == []
    # 数据与树都还在
    assert prefix.is_dir(), "默认卸载不得删除应用树"
    assert (prefix / "output" / "precious.json").is_file(), "用户数据不得被删"


@needs_posix_bash
def test_uninstall_refuses_to_delete_a_tree_holding_user_data(tmp_path: Path) -> None:
    """★ `--remove-prefix` 撞上便携数据根 ⇒ 必须**拒绝**（非零）且树仍在。"""
    app = _make_app_tree(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    prefix = tmp_path / "prefix"
    assert _run(app / "installer" / "install-user.sh",
                ["--prefix", str(prefix), "--no-desktop-database"], home).returncode == 0

    refused = _run(prefix / "installer" / "uninstall-user.sh",
                   ["--prefix", str(prefix), "--remove-prefix"], home)
    assert refused.returncode != 0, "有用户数据时必须拒绝删树"
    assert prefix.is_dir()
    assert (prefix / "output" / "precious.json").is_file()

    # 显式 --purge-data 才允许删（不可逆，用户已二次确认）
    purged = _run(prefix / "installer" / "uninstall-user.sh",
                  ["--prefix", str(prefix), "--purge-data"], home)
    assert purged.returncode == 0, purged.stdout + purged.stderr
    assert not prefix.exists()


@needs_posix_bash
def test_cli_symlink_points_into_the_prefix(tmp_path: Path) -> None:
    app = _make_app_tree(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    prefix = tmp_path / "prefix"
    assert _run(app / "installer" / "install-user.sh",
                ["--prefix", str(prefix), "--no-desktop-database"], home).returncode == 0
    link = home / ".local" / "bin" / "omnicrawler"
    assert link.is_symlink()
    assert Path(os.readlink(link)) == prefix / "omnicrawler"


# ── I1b 第②层：归档级安装冒烟（tools/portable_archive_smoke.linux_install_smoke） ──


def _stage_release_root(tmp_path: Path) -> Path:
    """伪造一棵"已解压的 Linux 产物树"：入口 + `installer/`，与真产物同形。"""
    root = tmp_path / "OmniCrawler"
    (root / "installer" / "icons" / "hicolor").mkdir(parents=True)
    for name in DELIVERY_FILES:
        shutil.copy2(DELIVERY_DIR / name, root / "installer" / name)
    for size in HICOLOR_SIZES:
        dest = root / "installer" / "icons" / "hicolor" / f"{size}x{size}" / "apps"
        dest.mkdir(parents=True)
        shutil.copy2(BRANDING_SRC / f"omnicrawler-icon-{size}.png", dest / "omnicrawler.png")
    for entry in ("OmniCrawler", "omnicrawler"):
        stub = root / entry
        stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)
    return root


@needs_posix_bash
def test_archive_install_smoke_passes_on_a_faithful_tree(tmp_path: Path) -> None:
    root = _stage_release_root(tmp_path)
    result = archive_smoke.linux_install_smoke(root, tmp_path / "work")
    assert result["desktop_entry"] == "ok"
    assert result["exec_absolute_and_present"] is True
    assert result["hicolor_icons"] == len(HICOLOR_SIZES)
    # desktop-file-validate 可能没装：那时必须是**可见**的 skipped，而不是静默 ok
    assert "desktop-file-validate" in str(result["desktop_file_validate"])


@needs_posix_bash
def test_archive_install_smoke_goes_red_without_the_installer(tmp_path: Path) -> None:
    """★ 反向断言：安装件没进包 ⇒ 归档冒烟必须红（而不是"跳过安装步骤"）。"""
    root = _stage_release_root(tmp_path)
    (root / "installer" / "install-user.sh").unlink()
    with pytest.raises(FileNotFoundError):
        archive_smoke.linux_install_smoke(root, tmp_path / "work")


@needs_posix_bash
def test_archive_install_smoke_red_then_restored_when_exec_points_nowhere(
    tmp_path: Path,
) -> None:
    """★ 反向断言：`Exec=` 指到不存在的入口 ⇒ 必须红 ⇒ 字节还原后必须再通过。"""
    root = _stage_release_root(tmp_path)
    template = root / "installer" / "omnicrawler.desktop.in"
    pristine = template.read_bytes()
    broken = pristine.replace(b"Exec=@PREFIX@/OmniCrawler", b"Exec=@PREFIX@/NoSuchBinary")
    assert broken != pristine, "模板里应存在 Exec=@PREFIX@/OmniCrawler 这个锚点"

    template.write_bytes(broken)
    with pytest.raises(RuntimeError):
        archive_smoke.linux_install_smoke(root, tmp_path / "work")

    template.write_bytes(pristine)
    assert _sha256(template) == hashlib.sha256(pristine).hexdigest()
    assert archive_smoke.linux_install_smoke(root, tmp_path / "work")["desktop_entry"] == "ok"
