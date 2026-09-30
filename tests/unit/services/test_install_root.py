"""安装根与运行目录必须分开（2026-09-30 实测发现的数据丢失路径）。

背景（实测可复现）：`application_dir()` 是"正在运行的二进制在哪"，而冻结包在没有
`data-mode.json` 时**一定**把数据根设为它。于是 `versions/<v>/` 布局下每换一版，用户的
`work/`、`data/` 就跟着换地方；而 `remove_stale_version_dirs()` 的规则是"除 `current.txt`
指向的那份外全部 rmtree" ⇒ **下一轮 `cleanup --yes` 会把上一版目录连同里面的用户数据删掉**。

本文件钉三件事：① 安装根解析（环境变量 / 目录名推断 / 就地退化）；
② 数据根落在**安装根**而不是 `versions/<v>/`；③ 含用户数据的版本目录**不得**被清理。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from omnicrawler.core import runtime_paths as rp
from omnicrawler.services.updater import (
    UpgradeManager,
    holds_user_workspace,
    user_workspace_reason,
)


@pytest.fixture(autouse=True)
def _clean_caches() -> None:
    """`portable_data_root` 带 lru_cache，且测试要 monkeypatch 环境变量 ⇒ 每个用例前后清干净。"""
    rp.portable_data_root.cache_clear()
    yield
    rp.portable_data_root.cache_clear()


def _fake_frozen(monkeypatch: pytest.MonkeyPatch, exe: Path) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.delenv(rp.INSTALL_ROOT_ENV, raising=False)


def _layout_b(tmp_path: Path, version: str = "1.2.3") -> Path:
    """造一个布局 B 的安装：``<根>/versions/<版本>/OmniCrawler.exe``；返回安装根。"""
    root = tmp_path / "install"
    (root / "versions" / version).mkdir(parents=True, exist_ok=True)
    (root / "versions" / version / "OmniCrawler.exe").write_bytes(b"bin")
    (root / "PORTABLE.flag").write_bytes(b"")
    return root


def test_install_root_comes_from_env_when_launcher_passes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """启动器显式传 `OMNICRAWL_INSTALL_ROOT` ⇒ 就用它（这是主路径）。"""
    root = _layout_b(tmp_path)
    _fake_frozen(monkeypatch, root / "versions" / "1.2.3" / "OmniCrawler.exe")
    monkeypatch.setenv(rp.INSTALL_ROOT_ENV, str(root))

    assert rp.application_dir() == (root / "versions" / "1.2.3").resolve()
    assert rp.install_root() == root.resolve()
    assert rp.portable_data_root() == root.resolve()


def test_install_root_inferred_from_versions_dirsuffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 没有环境变量时的兜底：运行目录的父目录名为 `versions` ⇒ 上溯一层。

    这一层必须独立成立，因为"启动器是旧版（不会传环境变量）"与"用户直接跑二进制"
    都是真实场景 —— 少了它，数据根又漂回版本目录里。
    """
    root = _layout_b(tmp_path)
    _fake_frozen(monkeypatch, root / "versions" / "1.2.3" / "OmniCrawler.exe")

    assert rp.install_root() == root.resolve()
    assert rp.portable_data_root() == root.resolve()


def test_data_root_is_not_the_version_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """★★ 本文件的核心断言：数据根**必须**是安装根，**不得**是 `versions/<v>/`。

    一旦实现退化回 `application_dir()`，这条立刻转红 —— 它是"数据跟着版本漂移"的总闸。
    """
    root = _layout_b(tmp_path)
    version_dir = (root / "versions" / "1.2.3").resolve()
    _fake_frozen(monkeypatch, root / "versions" / "1.2.3" / "OmniCrawler.exe")

    data_root = rp.portable_data_root()

    assert data_root == root.resolve()
    assert data_root != version_dir
    assert "versions" not in data_root.relative_to(root).parts


def test_in_place_layout_keeps_both_roots_equal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """就地布局下两个根重合（不能为了修版本化布局而把就地布局改坏）。"""
    app = tmp_path / "OmniCrawler"
    app.mkdir()
    (app / "OmniCrawler.exe").write_bytes(b"bin")
    (app / "PORTABLE.flag").write_bytes(b"")
    _fake_frozen(monkeypatch, app / "OmniCrawler.exe")

    assert rp.install_root() == app.resolve()
    assert rp.portable_data_root() == app.resolve()


def test_env_pointing_nowhere_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 环境变量指向不存在的目录 ⇒ 忽略它并退到别的判据（不能因此把数据根设到虚空）。"""
    root = _layout_b(tmp_path)
    _fake_frozen(monkeypatch, root / "versions" / "1.2.3" / "OmniCrawler.exe")
    monkeypatch.setenv(rp.INSTALL_ROOT_ENV, str(tmp_path / "does-not-exist"))

    assert rp.install_root() == root.resolve()


def test_cleanup_never_deletes_version_dir_holding_user_workspace(tmp_path: Path) -> None:
    """★ 数据丢失守卫：**含用户数据**的旧版本目录不得被删，且必须如实报出来。

    场景正是那条真实路径：旧版本目录里已经写进了用户的 `work/`（因为那时数据根还漂着），
    随后 `cleanup --yes` 按"除当前版外全部"回收 —— 没有这条守卫就会 rmtree 掉用户数据。
    """
    app = tmp_path / "app"
    (app / "versions" / "0.4.0" / "work").mkdir(parents=True)
    (app / "versions" / "0.4.0" / "work" / "task.db").write_bytes(b"USER DATA")
    (app / "versions" / "0.4.0" / "app.exe").write_bytes(b"LEGACY")
    (app / "versions" / "current.txt").write_text("0.6.4\n", encoding="utf-8")
    manager = UpgradeManager(app, install_root=app)

    stale_names = [package.name for package, _size in manager.stale_version_dirs()]
    protected = manager.protected_version_dirs()

    assert "0.4.0" not in stale_names, "含用户数据的版本目录不得进入可清理集合"
    assert [(package.name, reason) for package, reason in protected] == [("0.4.0", "work/ 非空")]

    result = manager.remove_stale_version_dirs()

    assert result["removed"] == []
    assert (app / "versions" / "0.4.0" / "work" / "task.db").read_bytes() == b"USER DATA"
    assert result["protected"] == [{"path": "versions/0.4.0", "reason": "work/ 非空"}]


def test_cleanup_still_removes_version_dir_without_user_data(tmp_path: Path) -> None:
    """反向的另一半：**没有**用户数据的旧版本目录照旧被回收（守卫不能把清理功能废掉）。

    载荷里本来就含空的 `work/`、`data/`、`logs/`（构建脚本会建），所以判据必须是"非空"，
    而不是"目录存在"—— 否则每一份版本目录都受保护，清理形同虚设。
    """
    app = tmp_path / "app"
    (app / "versions" / "0.4.0" / "work").mkdir(parents=True)   # 空目录（载荷自带）
    (app / "versions" / "0.4.0" / "logs").mkdir(parents=True)   # logs 不参与判定
    (app / "versions" / "0.4.0" / "app.exe").write_bytes(b"LEGACY")
    (app / "versions" / "current.txt").write_text("0.6.4\n", encoding="utf-8")
    manager = UpgradeManager(app, install_root=app)

    result = manager.remove_stale_version_dirs()

    assert result["removed"] == ["0.4.0"]
    assert not (app / "versions" / "0.4.0").exists()
    assert result["protected"] == []


def test_logs_alone_do_not_protect_a_version_dir(tmp_path: Path) -> None:
    """★ 判据边界：**只**有 logs 有内容时仍算"可清理"。

    运行期必然写日志 ⇒ 若把 `logs` 也算"用户数据"，每份版本目录都不可清理（功能废掉）。
    这是"判据按真正被度量的对象设定"的一个具体落点。
    """
    app = tmp_path / "app"
    (app / "versions" / "0.4.0" / "logs").mkdir(parents=True)
    (app / "versions" / "0.4.0" / "logs" / "gui.log").write_bytes(b"log line")
    (app / "versions" / "current.txt").write_text("0.6.4\n", encoding="utf-8")
    manager = UpgradeManager(app, install_root=app)

    assert [package.name for package, _ in manager.stale_version_dirs()] == ["0.4.0"]
    assert manager.protected_version_dirs() == []


def test_unreadable_workspace_is_treated_as_holding_data(tmp_path: Path) -> None:
    """读不了（权限/占用）⇒ 保守视为"有数据"，宁可留着也不赌。"""
    version_dir = tmp_path / "versions" / "0.4.0"
    (version_dir / "data").mkdir(parents=True)

    def _boom(*_args: object, **_kwargs: object):
        raise OSError("access denied")

    import pathlib

    original = pathlib.Path.iterdir

    def _patched(self: pathlib.Path):  # type: ignore[no-untyped-def]
        if self.name == "data":
            raise OSError("access denied")
        return original(self)

    import unittest.mock as mock

    with mock.patch.object(pathlib.Path, "iterdir", _patched):
        assert holds_user_workspace(version_dir) is True
        assert "无法读取" in user_workspace_reason(version_dir)


def test_holds_user_workspace_false_for_missing_dirs(tmp_path: Path) -> None:
    """什么都不存在（干净的版本目录）⇒ 不认为有用户数据。"""
    version_dir = tmp_path / "versions" / "0.9.9"
    version_dir.mkdir(parents=True)
    (version_dir / "app.exe").write_bytes(b"bin")

    assert holds_user_workspace(version_dir) is False
    assert user_workspace_reason(version_dir) == ""
