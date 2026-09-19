"""I2：Windows 首启快捷方式的守卫（ctypes 写入 + **读回**断言）。

为什么正确性只能靠"读回断言"
----------------------------

ctypes 误用（vtable 槽位错、签名错）是 **AV 崩进程**，不是 Python 异常，
``try/except`` 兜不住。所以"到底写进去没有"必须在 Windows 上把字段**读回来**
逐字比对 —— "文件存在" **不算**通过（红线：看起来正常 ≠ 运行时正确）。

★ 目标必须是**真可执行文件**：``IPersistFile::Save`` 会校验目标，指向一个只有
``MZ`` 头的假文件会被 ``E_FAIL`` 拒绝（本批实测）。这是**正确行为**，不是缺陷 ——
用例因此用 ``sys.executable`` 当目标。

单文件、单用例、分支内断言：非 Windows 时断言 ``unsupported``（真断言，不是跳过），
避免产生"不可见的跳过"。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from omnicrawler.core import win_shortcut

# Shell Link 头：前 4 字节是本结构自身的长度（0x0000004C，小端）⇒ 4C 00 00 00。
_SHELL_LINK_MAGIC = b"\x4c\x00\x00\x00"


def test_shortcut_round_trip_and_platform_contract(tmp_path: Path) -> None:
    if not win_shortcut.is_platform_supported():
        # 非 Windows：**真断言** —— 不支持就得说 unsupported，而且不能崩。
        result = win_shortcut.create_shortcut(tmp_path / "x.lnk", Path(sys.executable))
        assert result.ok is False
        assert result.status == "unsupported"
        assert win_shortcut.create_shortcut_for_app().status == "unsupported"
        assert not (tmp_path / "x.lnk").exists()
        return

    # ── Windows ──────────────────────────────────────────────────────────
    target = Path(sys.executable)
    assert target.is_file(), "用例需要一个真实的可执行文件当目标"

    link = tmp_path / "OmniCrawler.lnk"
    result = win_shortcut.create_shortcut(
        link, target, working_dir=tmp_path, icon=f"{target},0", description="OmniCrawler"
    )
    assert result.ok is True, result.detail
    assert result.status == "created"
    assert link.is_file()
    assert link.read_bytes()[:4] == _SHELL_LINK_MAGIC

    # ★ 读回三个字段逐字比对（"文件存在"不算通过）
    fields = win_shortcut.read_back(link)
    assert Path(fields["target"]) == target
    assert Path(fields["working_dir"]) == tmp_path
    assert fields["icon"].startswith(str(target))
    assert fields["icon_index"] == "0"

    # 目标不存在 ⇒ 明确失败（不是"静默成功"）
    missing = win_shortcut.create_shortcut(tmp_path / "nope.lnk", tmp_path / "no-such.exe")
    assert missing.ok is False
    assert missing.status == "failed"
    assert not (tmp_path / "nope.lnk").exists()

    # 源码运行（未打包）没有可指的 exe ⇒ unsupported：不弹、不建
    assert win_shortcut.create_shortcut_for_app().status == "unsupported"

    # 默认落点＝桌面 + 开始菜单，且都是 per-user 的 .lnk（不写文件，只解析路径）
    defaults = win_shortcut.default_shortcut_paths()
    assert defaults, "Windows 上应能解析出桌面/开始菜单目录"
    assert all(path.name == "OmniCrawler.lnk" for path in defaults)


# ── 平台中立的编排层（任何平台都能跑）────────────────────────────────────
# `create_shortcut` / `create_shortcut_for_app` 刻意不含 COM 调用：真正碰 COM 的只有
# `_write_link_via_com`（Windows-only，由 windows-latest 的读回断言看住）。这里把那条
# 接缝换成替身 ⇒「平台判定 / 目标校验 / 父目录创建 / 异常映射 / 多落点聚合」在任何平台
# 都被覆盖，而不是只能在 Windows runner 上被度量。


def _force_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(win_shortcut, "is_platform_supported", lambda: True)


def test_create_shortcut_orchestration_without_com(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """写入口换成替身后，编排层的三条分支都要有断言。"""
    _force_windows(monkeypatch)
    recorded: list[dict[str, object]] = []

    def fake_write(link_path, target, *, working_dir, icon, description):  # noqa: ANN001, ANN202
        recorded.append({"link": link_path, "target": target, "dir": working_dir, "icon": icon})

    monkeypatch.setattr(win_shortcut, "_write_link_via_com", fake_write)
    target = tmp_path / "app.exe"
    target.write_bytes(b"MZ")

    nested = tmp_path / "deep" / "OmniCrawler.lnk"  # 父目录不存在：应由编排层创建
    ok = win_shortcut.create_shortcut(nested, target, working_dir=tmp_path, icon=f"{target},0")
    assert ok.ok is True
    assert ok.status == "created"
    assert nested.parent.is_dir(), "编排层必须建好父目录"
    assert recorded and recorded[0]["link"] == nested and recorded[0]["target"] == target

    def failing_write(*_args: object, **_kwargs: object) -> None:
        raise OSError("com blew up")

    monkeypatch.setattr(win_shortcut, "_write_link_via_com", failing_write)
    failed = win_shortcut.create_shortcut(tmp_path / "b.lnk", target)
    assert failed.ok is False
    assert failed.status == "failed"
    assert "com blew up" in failed.detail, "失败原因必须原样带出，不能吞掉"

    # 目标不存在：早退，且**不**触碰写入口
    monkeypatch.setattr(win_shortcut, "_write_link_via_com", fake_write)
    missing = win_shortcut.create_shortcut(tmp_path / "c.lnk", tmp_path / "nope.exe")
    assert missing.ok is False and "does not exist" in missing.detail


def test_create_shortcut_for_app_aggregates_outcomes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """多落点聚合的三条分支（全部成功 / 部分成功 / 全失败）+ 无入口时的 unsupported。"""
    _force_windows(monkeypatch)
    target = tmp_path / "OmniCrawler.exe"
    target.write_bytes(b"MZ")
    monkeypatch.setattr(win_shortcut, "resolve_target", lambda: target)
    destinations = (tmp_path / "desktop.lnk", tmp_path / "startmenu.lnk")

    monkeypatch.setattr(
        win_shortcut,
        "create_shortcut",
        lambda link, *_a, **_k: win_shortcut.ShortcutResult(
            ok=True, status="created", created=(link,)
        ),
    )
    all_ok = win_shortcut.create_shortcut_for_app(destinations)
    assert all_ok.ok is True and all_ok.created == destinations

    outcomes = iter(
        (
            win_shortcut.ShortcutResult(ok=True, status="created", created=(destinations[0],)),
            win_shortcut.ShortcutResult(ok=False, status="failed", detail="nope"),
        )
    )
    monkeypatch.setattr(win_shortcut, "create_shortcut", lambda *_a, **_k: next(outcomes))
    partial = win_shortcut.create_shortcut_for_app(destinations)
    assert partial.ok is False and partial.created == (destinations[0],)
    assert "nope" in partial.detail

    monkeypatch.setattr(
        win_shortcut,
        "create_shortcut",
        lambda *_a, **_k: win_shortcut.ShortcutResult(ok=False, status="failed", detail="all bad"),
    )
    none_ok = win_shortcut.create_shortcut_for_app(destinations)
    assert none_ok.ok is False and none_ok.created == () and "all bad" in none_ok.detail

    monkeypatch.setattr(win_shortcut, "resolve_target", lambda: None)
    assert win_shortcut.create_shortcut_for_app(destinations).status == "unsupported"
