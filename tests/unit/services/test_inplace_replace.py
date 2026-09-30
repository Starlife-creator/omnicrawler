"""就地替换引擎：**改名让位 + 待清理挂账**（不复制副本、可回滚）。

对应 issue #88 落地形态那一条，判据全部做成可失败的：

* 替换一个**存在**的文件 = 先改名让位、再写新内容 —— 旧文件绝不先被复制一份（省空间）；
* 旧文件**当场删不掉**（正被占用）⇒ 必须**挂账**，并在**下次运行时**清掉（"下次启动清理"）；
* 失败时**回滚靠改名**（删掉新文件、把 ``.old-*`` 改名回去），而不是靠备份副本；
* ★ 反向断言：**"只替换清单里的文件"** —— 没被替换的文件其**内容与 mtime 都必须原封不动**，
  一旦实现退化成"把包里所有成员都写一遍"，这条立刻转红（见 `..._leaves_untouched_files_alone`）。
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

from omnicrawler.services import updater as up
from omnicrawler.services.updater import (
    ASIDE_MARKER,
    PENDING_FILENAME,
    UpgradeManager,
)


def _key() -> bytes:
    return b"0" * 32  # stage 不走签名校验时用不到；本文件全部直接喂 stage 目录


def _manager(root: Path) -> UpgradeManager:
    return UpgradeManager(root, trusted_public_key=_key())


def _stage(root: Path, files: dict[str, bytes]) -> Path:
    """直接在 `.updates/staging/<name>/` 下造一个暂存目录（绕开 zip/签名，专测替换）。"""
    stage = root / ".updates" / "staging" / f"t{time.time_ns()}"
    for relative, body in files.items():
        target = stage.joinpath(*Path(relative).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    return stage


# ── ① 改名让位：旧文件不复制、新内容就位、残留被清 ─────────────────────


def test_replace_renames_aside_instead_of_copying_a_backup(tmp_path: Path) -> None:
    root = tmp_path / "app"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "app.exe").write_bytes(b"OLD")
    manager = _manager(root)

    result = manager.apply(_stage(root, {"bin/app.exe": b"NEW"}))

    assert (root / "bin" / "app.exe").read_bytes() == b"NEW"
    assert result["applied"] == 1
    assert result["removed"] == 1              # 改名让位出来的旧文件当场删掉了
    assert result["pending_cleanup"] == []
    # ★ 没有 rollback 目录/备份副本（省空间的关键）
    assert not (root / ".updates" / "rollback").exists()
    assert list((root / "bin").glob(f"*{ASIDE_MARKER}*")) == []


def test_replace_creates_missing_file_without_aside(tmp_path: Path) -> None:
    root = tmp_path / "app"
    root.mkdir()
    manager = _manager(root)
    result = manager.apply(_stage(root, {"new.txt": b"X"}))
    assert (root / "new.txt").read_bytes() == b"X"
    assert result["applied"] == 1 and result["removed"] == 1


# ── ② 删不掉 ⇒ 挂账 ⇒ 下次运行清掉 ────────────────────────────────────


def test_locked_leftover_is_recorded_then_cleaned_next_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ "改名让位 + 下次启动清理"的核心行为。

    "改名能成功、删除却失败"只在**正在运行的程序文件**上发生（实测），单元测试里造不出来
    ⇒ 把删除点替换成"抛 OSError"，确定性地验证挂账与收口。
    """
    root = tmp_path / "app"
    root.mkdir()
    (root / "qt.dll").write_bytes(b"OLD")
    manager = _manager(root)

    real_discard = up._discard

    def refuse(path: Path) -> None:
        if ASIDE_MARKER in path.name:
            raise OSError(32, "另一个程序正在使用此文件")
        real_discard(path)

    monkeypatch.setattr(up, "_discard", refuse)
    result = manager.apply(_stage(root, {"qt.dll": b"NEW"}))

    assert (root / "qt.dll").read_bytes() == b"NEW"      # 新内容照样就位
    assert result["removed"] == 0 and len(result["pending_cleanup"]) == 1
    pending_path = root / ".updates" / PENDING_FILENAME
    assert pending_path.is_file()
    assert manager.pending_cleanup() == result["pending_cleanup"]

    # 下次运行（＝"下次启动"）：这次删得掉 ⇒ 清空并收敛清单
    monkeypatch.setattr(up, "_discard", real_discard)
    cleanup = manager.run_pending_cleanup()
    assert cleanup["removed"] == 1 and cleanup["remaining"] == []
    assert manager.pending_cleanup() == []
    assert list(root.glob(f"*{ASIDE_MARKER}*")) == []


def test_pending_cleanup_keeps_undeletable_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """仍然删不掉（文件还在被占用）⇒ 继续留在清单里，不能假装清完了。"""
    root = tmp_path / "app"
    root.mkdir()
    leftover = root / f"qt.dll{ASIDE_MARKER}abc"
    leftover.write_bytes(b"L")
    manager = _manager(root)
    manager.record_pending([leftover.name])

    monkeypatch.setattr(up, "_discard", lambda path: (_ for _ in ()).throw(OSError(32, "busy")))
    cleanup = manager.run_pending_cleanup()
    assert cleanup["removed"] == 0
    assert cleanup["remaining"] == [leftover.name]
    assert manager.pending_cleanup() == [leftover.name]


def test_pending_cleanup_ignores_missing_files_and_corrupt_manifest(tmp_path: Path) -> None:
    """清单里记的文件已经不在（用户自己删了）⇒ 不算残留；清单损坏 ⇒ 当作空，不阻塞升级。"""
    root = tmp_path / "app"
    root.mkdir()
    manager = _manager(root)
    manager.record_pending(["gone.dll.old-1"])
    assert manager.pending_cleanup() == []

    (root / ".updates" / PENDING_FILENAME).write_text("{ not json", encoding="utf-8")
    assert manager.pending_cleanup() == []
    assert manager.run_pending_cleanup() == {"removed": 0, "remaining": []}


# ── ③ 回滚：靠改名，不靠副本 ────────────────────────────────────────────


def test_failure_rolls_back_by_renaming_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """写第二个文件时失败 ⇒ 第一个文件必须**原名原内容**复原，且不留备份副本。"""
    root = tmp_path / "app"
    root.mkdir()
    (root / "a.txt").write_bytes(b"A-OLD")
    (root / "b.txt").write_bytes(b"B-OLD")
    manager = _manager(root)
    stage = _stage(root, {"a.txt": b"A-NEW", "b.txt": b"B-NEW"})

    real_copy = up.shutil.copy2

    def explode(source: object, target: object, *args: object, **kwargs: object) -> object:
        if Path(str(target)).name == "b.txt":
            raise OSError(28, "磁盘满了")
        return real_copy(source, target, *args, **kwargs)

    monkeypatch.setattr(up.shutil, "copy2", explode)
    with pytest.raises(OSError):
        manager.apply(stage)

    assert (root / "a.txt").read_bytes() == b"A-OLD"   # 改名还原
    assert (root / "b.txt").read_bytes() == b"B-OLD"
    assert list(root.glob(f"*{ASIDE_MARKER}*")) == []  # 没有留下残留
    assert manager.pending_cleanup() == []


# ── ④ 反向断言：只动清单里的文件 ───────────────────────────────────────


def test_apply_leaves_untouched_files_alone(tmp_path: Path) -> None:
    """★ 反向断言：**没被替换的文件，内容与 mtime 都必须原封不动**。

    "只换变化的那几个"这件事如果实现退化成"把包里所有成员都写一遍"（或反过来
    "把整个目录重写"），未变文件的 mtime 就会变 ⇒ 本用例转红。
    这正是"不会真的下 2G / 不会白写一遍"的可失败判据。
    """
    root = tmp_path / "app"
    (root / "_internal").mkdir(parents=True)
    heavy = root / "_internal" / "qt.dll"
    heavy.write_bytes(b"QT" * 500)
    (root / "app.exe").write_bytes(b"OLD")
    before = heavy.stat().st_mtime_ns

    manager = _manager(root)
    time.sleep(0.01)
    manager.apply(_stage(root, {"app.exe": b"NEW"}))  # 只替换 app.exe

    assert heavy.read_bytes() == b"QT" * 500
    assert heavy.stat().st_mtime_ns == before, "未变的重依赖被重写了（说明没有真跳过）"
    assert (root / "app.exe").read_bytes() == b"NEW"


# ── ⑤ versions/ 旧版本的识别与清理（布局 B 的收口）──────────────────────


def test_stale_version_dirs_skip_the_pointed_one(tmp_path: Path) -> None:
    manager = UpgradeManager(tmp_path, trusted_public_key=None)
    (tmp_path / "versions" / "0.98.0").mkdir(parents=True)
    (tmp_path / "versions" / "0.98.0" / "app.exe").write_bytes(b"x" * 100)
    (tmp_path / "versions" / "99.0.0").mkdir()
    (tmp_path / "versions" / "99.0.0" / "app.exe").write_bytes(b"y" * 10)
    (tmp_path / "versions" / "current.txt").write_text("99.0.0\n", encoding="utf-8")

    stale = manager.stale_version_dirs()
    assert [package.name for package, _ in stale] == ["0.98.0"]
    assert stale[0][1] == 100


def test_cleanup_without_pointer_marks_every_version_dir_stale(tmp_path: Path) -> None:
    """就地布局（无指针）下若存在 versions/ 目录 ⇒ 全部算残留（没有指针就说明它们不在用）。"""
    manager = UpgradeManager(tmp_path, trusted_public_key=None)
    (tmp_path / "versions" / "0.4.0").mkdir(parents=True)
    (tmp_path / "versions" / "0.4.0" / "app.exe").write_bytes(b"z" * 5)
    assert [package.name for package, _ in manager.stale_version_dirs()] == ["0.4.0"]


def test_remove_stale_version_dirs_keeps_pointed_and_reports_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = UpgradeManager(tmp_path, trusted_public_key=None)
    stale = tmp_path / "versions" / "0.4.0"
    stale.mkdir(parents=True)
    (stale / "app.exe").write_bytes(b"x" * 5)

    real_rmtree = shutil.rmtree

    def refuse(path: object, *args: object, **kwargs: object) -> None:
        raise OSError(32, "busy")

    monkeypatch.setattr(shutil, "rmtree", refuse)
    result = manager.remove_stale_version_dirs()
    assert result["removed"] == [] and result["failed"] == [
        "versions/0.4.0"
    ], "删不掉必须如实报告，不能假装清完"
    assert stale.is_dir()

    monkeypatch.setattr(shutil, "rmtree", real_rmtree)
    result = manager.remove_stale_version_dirs()
    assert result["removed"] == ["0.4.0"] and result["freed_bytes"] == 5
    assert not stale.exists()


# ── 全量路径的用户所有区域保护（#88 验收要求 5）────────────────────────────
# 增量路径由 `plan_payload` 把 `configs/**`（信任根除外）排除在 `to_fetch` 之外；
# 而**全量路径是按压缩档成员走的、根本不看 plan** ⇒ 必须在 `apply_archive` 里补同一道判据。
# 两处共用 `update_feed.is_user_owned`（判据同源），避免"增量保护了、全量没保护"。


def _zip_package(root: Path, files: dict[str, bytes], *, strip_root: str = "OmniCrawler"):
    import hashlib
    import zipfile

    path = root.parent / f"pkg-{time.time_ns()}.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for relative, body in sorted(files.items()):
            archive.writestr(f"{strip_root}/{relative}", body)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_full_archive_preserves_a_user_modified_config(tmp_path: Path) -> None:
    """★ 用户改过的 `configs/` 在全量路径下也必须活下来（旧行为会静默冲掉）。

    同时钉住**例外**：信任根照常更新（否则轮换密钥后更新永久坏掉）、
    以及"保护是不覆盖而不是不安装"（新默认项照装）。
    """
    app = tmp_path / "app"
    (app / "configs").mkdir(parents=True)
    (app / "configs" / "project.yaml").write_bytes(b"USER-EDITED")
    (app / "configs" / "update_trust.pub.pem").write_bytes(b"OLD-ROOT")
    (app / "runtime").mkdir()
    (app / "runtime" / "app.bin").write_bytes(b"OLD")
    (app / "work").mkdir()
    (app / "work" / "keep.db").write_bytes(b"USER DATA")

    package, digest = _zip_package(
        app,
        {
            "configs/project.yaml": b"SHIPPED-DEFAULT",
            "configs/update_trust.pub.pem": b"NEW-ROOT",
            "configs/brand_new.yaml": b"NEW-DEFAULT",
            "runtime/app.bin": b"NEW",
        },
    )
    result = _manager(app).apply_archive(
        package, strip_root="OmniCrawler", expected_sha256=digest, dest_root=app
    )

    assert (app / "configs" / "project.yaml").read_bytes() == b"USER-EDITED", "用户改过的配置被冲掉"
    assert (app / "runtime" / "app.bin").read_bytes() == b"NEW", "非用户所有区域照常更新"
    assert (app / "configs" / "update_trust.pub.pem").read_bytes() == b"NEW-ROOT", "信任根要能轮换"
    assert (app / "configs" / "brand_new.yaml").read_bytes() == b"NEW-DEFAULT", "新默认项照装"
    assert (app / "work" / "keep.db").read_bytes() == b"USER DATA", "工作区数据不动"
    assert result["preserved"] == ["configs/project.yaml"], "保留了哪些必须如实报出"
