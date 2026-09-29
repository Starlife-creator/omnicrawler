from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import zipfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from ..core.archive_security import (
    DEFAULT_ZIP_READ_LIMITS,
    copy_zip_member,
    read_zip_member,
    validate_zip_archive,
)
from .component_manager import _verify_ed25519

#: 升级包**永远不许触碰**的顶层路径（越界即整包拒绝）。
#:
#: 判据是"合法升级**绝不**需要写它"：
#: * `work` / `data` / `output` / `logs` / `.omnicrawler`：用户数据与密钥；
#: * `plugins_installed`：**市场安装的插件**（构建脚本从不产出它，纯运行期数据，
#:   合法升级包没有任何理由写它）—— 此前漏在名单外，升级包可以覆盖用户已装插件；
#: * `PORTABLE.flag` / `portable.flag`：便携模式标记（写错会改掉数据根判定）。
#:
#: ★ **`configs/` 刻意不在名单里**：它是**随包目录**（`configs/plugin_trust.pub.pem`
#: 是内置信任根，由构建脚本 `build_*.ps1/sh` 随包复制），整目录保护会让
#: "升级时轮换信任根"永远做不到。故对 `configs/` 的约束是"**只允许随包文件被更新**"，
#: 而不是"禁止触碰" —— 不要为了"看起来更安全"把它加进来。
PROTECTED_TOP_LEVEL = {
    "work",
    "data",
    "output",
    "logs",
    ".omnicrawler",
    "plugins_installed",
    "PORTABLE.flag",
    "portable.flag",
}

#: 改名让位时给旧文件加的后缀（同一个目录内、同卷 ⇒ 改名总是很快）。
ASIDE_MARKER = ".old-"

#: 待清理清单（相对路径）。改名让位后的旧文件若**当场删不掉**（正被占用），
#: 就登记在这里，等下一次运行（＝"下次启动"）再删。
PENDING_FILENAME = "pending-cleanup.json"


class UpgradeManager:
    def __init__(self, app_root: Path, *, trusted_public_key: bytes | None = None) -> None:
        # trusted_public_key 只有 stage/stage_members（取包）需要；清理类操作传 None 即可。
        self.app_root = app_root.resolve()
        self.trusted_public_key = trusted_public_key
        self.updates = self.app_root / ".updates"

    # ── 打包体：校验 + 展开（既有行为不变）────────────────────────────
    def stage(self, package: Path) -> dict[str, Any]:
        if self.trusted_public_key is None:
            raise ValueError("取包需要信任根公钥（清理类操作不需要，请勿用 None 调用）")
        with zipfile.ZipFile(package) as archive:
            members = validate_zip_archive(
                archive, required=("upgrade.json",), limits=DEFAULT_ZIP_READ_LIMITS
            )
            raw = json.loads(
                read_zip_member(
                    archive, members["upgrade.json"], maximum_bytes=DEFAULT_ZIP_READ_LIMITS.max_manifest_bytes
                )
            )
            signature = str(raw.pop("signature", ""))
            canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            _verify_ed25519(self.trusted_public_key, canonical, signature)
            files = raw.get("files", {})
            stage = self.updates / "staging" / f"{raw.get('version', 'unknown')}-{time.time_ns()}"
            stage.mkdir(parents=True, exist_ok=False)
            for name, expected in files.items():
                relative = _safe_upgrade_path(str(name))
                member = members.get(str(name))
                if member is None or member.is_dir():
                    raise ValueError(f"升级包缺少文件: {name}")
                target = stage.joinpath(*relative.parts)
                if copy_zip_member(archive, member, target) != expected:
                    raise ValueError(f"升级文件哈希不匹配: {name}")
        return {"stage": str(stage), "version": raw.get("version"), "files": len(files)}

    def stage_members(
        self, package: Path, expected: Mapping[str, str], *, version: str = "unknown"
    ) -> dict[str, Any]:
        """展开**变更包**：逐成员按**已签名清单**里的 sha256 核对后写入暂存目录。

        变更包自身**不需要单独签名**：它的每个成员都要对已签名清单里的哈希，
        改一个字节即整包拒绝；清单里列了而包里没有的文件同样拒绝
        （宁可拒绝，也不要"少换几个文件"这种静默不一致）。

        信任根仅在清理类操作下可为 None；展开变更包不需要验签，但取包需要。
        """
        if self.trusted_public_key is None:
            raise ValueError("展开变更包需要信任根公钥（清理类操作不需要）")
        if not expected:
            raise ValueError("变更包没有可比对的清单（expected 为空）")
        with zipfile.ZipFile(package) as archive:
            members = validate_zip_archive(archive, limits=DEFAULT_ZIP_READ_LIMITS)
            stage = self.updates / "staging" / f"{version}-{time.time_ns()}"
            stage.mkdir(parents=True, exist_ok=False)
            for name, digest in expected.items():
                relative = _safe_upgrade_path(str(name))
                member = members.get(str(name))
                if member is None or member.is_dir():
                    raise ValueError(f"变更包缺少清单里的文件: {name}")
                target = stage.joinpath(*relative.parts)
                if copy_zip_member(archive, member, target) != digest:
                    raise ValueError(f"变更包成员哈希与清单不符: {name}")
        return {"stage": str(stage), "version": version, "files": len(expected)}

    # ── 就地替换：改名让位，不复制副本 ────────────────────────────────
    def apply(self, stage: Path) -> dict[str, Any]:
        """把暂存目录里的文件**就地替换**进应用根。

        ★ 手法是**改名让位**而不是覆盖——Windows 不允许覆盖/删除正在运行的程序文件
        （实测 `PermissionError: 5`），但**允许改名**（实测成功）。逐文件：

        1. 旧文件改名成 ``<原名>.old-<随机>``（原名随即空出来）；
        2. 新文件写到**原名**上；
        3. 尝试删掉那个 ``.old-*``：删得掉 ⇒ 磁盘上只有一份；**删不掉**（仍被占用）
           ⇒ 登记进 `.updates/pending-cleanup.json`，等下次运行再删。

        ★ **回滚不复制副本**：任何一步失败，就把新写的文件删掉、把 ``.old-*`` 改名回去
        （同卷 rename，瞬时、零额外空间）——这比"先复制一份备份"省一整个载荷的空间。
        """
        stage = stage.resolve()
        staging_root = (self.updates / "staging").resolve()
        if staging_root not in stage.parents:
            raise ValueError("升级暂存目录无效")

        applied: list[tuple[Path, Path | None]] = []
        try:
            for source in sorted(path for path in stage.rglob("*") if path.is_file()):
                relative = source.relative_to(stage)
                _safe_upgrade_path(relative.as_posix())
                destination = self.app_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                aside: Path | None = None
                if destination.exists():
                    aside = _aside_path(destination)
                    os.replace(destination, aside)
                # ★ 先记账再写：这样"写新文件失败"也能回滚（不然旧文件已改名、无人还原）
                applied.append((destination, aside))
                shutil.copy2(source, destination)
        except Exception:
            self._rollback(applied)
            raise

        return {"applied": len(applied), **self._finish(applied), "workspace_preserved": True}

    def apply_archive(
        self,
        archive_path: Path,
        *,
        strip_root: str | None = None,
        expected_sha256: str | None = None,
        dest_root: Path | None = None,
    ) -> dict[str, Any]:
        """把一个**整包压缩档**就地替换进应用根（全量路径）。

        与 `apply()` 共用同一套**改名让位**机制（旧文件改名让位 → 写新内容 → 删掉或挂账），
        差别只在内容来源是压缩档而非暂存目录，因此**不需要先解压出一份暂存**
        （515MB／1.9GB 的包若先解压，峰值磁盘会直接翻倍）。

        - ``strip_root``：压缩档里包着一层顶层目录（本仓便携包是 ``OmniCrawler/``）⇒ 剥掉后再对位；
        - ``expected_sha256``：整包哈希（来自**已签名清单**的 ``assets[key].sha256``）——
          先核包再落地，**一个字节不符就一个文件都不写**；
        - ``dest_root``：写入根（默认应用根；"装到 versions/<ver>/" 时传该目录）。
        """
        if expected_sha256 is None:
            raise ValueError("整包必须提供 expected_sha256（来自已签名清单），否则无从校验")
        target_root = (dest_root or self.app_root).resolve()
        with zipfile.ZipFile(archive_path) as archive:
            digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
            if digest != expected_sha256:
                raise ValueError(
                    f"整包 sha256 与已签名清单不一致（期望 {expected_sha256}，实际 {digest}）"
                )
            members = validate_zip_archive(archive, limits=DEFAULT_ZIP_READ_LIMITS)
            prefix = f"{strip_root.strip('/')}/" if strip_root else ""
            applied: list[tuple[Path, Path | None]] = []
            try:
                for name, info in sorted(members.items()):
                    if info.is_dir():
                        continue
                    relative_name = name[len(prefix):] if prefix and name.startswith(prefix) else name
                    if not relative_name:
                        continue
                    relative = _safe_upgrade_path(relative_name)
                    destination = target_root / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    aside: Path | None = None
                    if destination.exists():
                        aside = _aside_path(destination)
                        os.replace(destination, aside)
                    applied.append((destination, aside))
                    if copy_zip_member(archive, info, destination) is None:
                        raise ValueError(f"整包成员写出失败: {name}")
            except Exception:
                self._rollback(applied)
                raise

        return {"applied": len(applied), **self._finish(applied), "workspace_preserved": True}

    def _rollback(self, applied: list[tuple[Path, Path | None]]) -> None:
        """尽力回滚：删掉新文件、把改名让位的旧文件改回原名。

        ``applied`` 的第一项是**绝对目标路径**（这样"装到 versions/" 时也能正确还原）。
        """
        leftovers: list[str] = []
        for destination, aside in reversed(applied):
            try:
                if aside is not None and aside.exists():
                    destination.unlink(missing_ok=True)
                    os.replace(aside, destination)
                elif aside is None:
                    destination.unlink(missing_ok=True)
            except OSError:
                # 回滚失败也不能再抛（会掩盖原始异常）；把残留登记进待清理，别静默丢下
                if aside is not None and aside.exists():
                    leftovers.append(aside.relative_to(self.app_root).as_posix())
        if leftovers:
            self._record_pending(leftovers)

    def _finish(self, applied: list[tuple[Path, Path | None]]) -> dict[str, Any]:
        """收尾：删掉改名让位出来的旧文件；删不掉的挂账。"""
        pending: list[str] = []
        for _relative, aside in applied:
            if aside is None or not aside.exists():
                continue
            try:
                _discard(aside)
            except OSError:
                pending.append(aside.relative_to(self.app_root).as_posix())
        if pending:
            self._record_pending(pending)
        return {"removed": len(applied) - len(pending), "pending_cleanup": pending}

    # ── 待清理清单（"下次启动清理"的落点）─────────────────────────────
    @property
    def pending_file(self) -> Path:
        return self.updates / PENDING_FILENAME

    def pending_cleanup(self) -> list[str]:
        """当前仍登记着、且文件确实还在的残留（相对路径，已排序去重）。"""
        recorded = _read_pending(self.pending_file)
        alive = [
            relative
            for relative in recorded
            if (self.app_root / relative).exists()
        ]
        return sorted(set(alive))

    def run_pending_cleanup(self) -> dict[str, Any]:
        """尽量删掉上次挂账的残留；删不掉的继续留着。**幂等、不抛异常。**"""
        remaining: list[str] = []
        removed = 0
        for relative in self.pending_cleanup():
            try:
                _discard(self.app_root / relative)
                removed += 1
            except OSError:
                remaining.append(relative)
        _write_pending(self.pending_file, remaining)
        return {"removed": removed, "remaining": remaining}

    def record_pending(self, relatives: list[str]) -> None:
        """把"当场删不掉"的残留登记进待清理清单（供调用方在删除失败后调用）。"""
        if relatives:
            self._record_pending(relatives)

    # ── 布局 B：versions/ 旧版本的识别与清理 ──────────────────────────
    @property
    def versions_root(self) -> Path:
        return self.app_root / "versions"

    @property
    def current_pointer_file(self) -> Path:
        return self.versions_root / "current.txt"

    def current_pointed_version(self) -> str:
        """布局 B 指针指向的版本（文件缺失/损坏 ⇒ 空串）。"""
        try:
            return self.current_pointer_file.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def stale_version_dirs(self) -> list[tuple[Path, int]]:
        """返回"可清理的旧版本目录"（绝对路径 + 体积字节）。

        规则：布局 B 下，除 `current.txt` 指向的那份外全部算旧版本；
        就地布局（无指针）下若存在 versions/ 目录，则其中**全部**目录都算残留
        （没有指针就说明当前生效的不是它们）。
        """
        versions = self.versions_root
        if not versions.is_dir():
            return []
        pointed = self.current_pointed_version()
        stale: list[tuple[Path, int]] = []
        for package in sorted(versions.iterdir()):
            if not package.is_dir():
                continue
            if pointed and package.name == pointed:
                continue
            size = sum(f.stat().st_size for f in package.rglob("*") if f.is_file())
            stale.append((package, size))
        return stale

    def remove_stale_version_dirs(self) -> dict[str, Any]:
        """删除全部旧版本目录；删不掉（文件被占用）的原样保留并如实返回。"""
        freed = 0
        failed: list[str] = []
        removed: list[str] = []
        for package, size in self.stale_version_dirs():
            try:
                shutil.rmtree(package)
                freed += size
                removed.append(package.name)
            except OSError:
                failed.append(package.relative_to(self.app_root).as_posix())
        return {"removed": removed, "failed": failed, "freed_bytes": freed}


    def _record_pending(self, relatives: list[str]) -> None:
        recorded = _read_pending(self.pending_file)
        _write_pending(self.pending_file, sorted(set(recorded) | set(relatives)))


def _aside_path(destination: Path) -> Path:
    """给同一个文件生成"改名让位"目标名（同目录、同卷）。"""
    return destination.with_name(f"{destination.name}{ASIDE_MARKER}{time.time_ns():x}")


def _discard(path: Path) -> None:
    """删除一个改名让位出来的残留文件。

    单独抽成函数是为了让"删不掉"这条分支**可确定性地测试**：真实触发它需要一个
    "改名能成功、删除却失败"的文件（＝正在运行的程序文件，实测如此），单元测试里
    造不出来 ⇒ 测试把这个点替换成"抛 OSError"，从而验证挂账路径。
    """
    path.unlink()


def _read_pending(path: Path) -> list[str]:
    """读待清理清单；损坏时**当作空**并保留文件（不阻塞升级，也不静默删掉别人的记录）。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, dict):
        return []
    entries = raw.get("files")
    if not isinstance(entries, list):
        return []
    return [str(item) for item in entries if isinstance(item, str) and item.strip()]


def _write_pending(path: Path, relatives: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"files": list(relatives), "updated_at": int(time.time())}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _safe_upgrade_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] in PROTECTED_TOP_LEVEL:
        raise ValueError(f"升级包包含受保护或不安全路径: {value}")
    return path
