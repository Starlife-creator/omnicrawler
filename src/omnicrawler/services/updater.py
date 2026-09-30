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
from ..core.runtime_paths import VERSIONS_DIRNAME
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

#: 判断一个版本目录里"是否已经有用户数据"时看的目录（与 `_FORBIDDEN_TOP_LEVEL` 同源）。
#:
#: ★ 只用**用户一定会产出文件的**那几个：`work`（任务工作区）、`data`（输入/库）、
#: `output`（导出）、`.omnicrawler`（密钥/缓存/挂账）。**故意不含 `logs`**：日志是
#: 构建期就会在载荷里生成的（`build_*.ps1/sh` 建了 `logs/` 且运行期必然写它），
#: 拿它当"有用户数据"会把**每一份**版本目录都变成受保护 ⇒ 清理功能形同废掉。
WORKSPACE_MARKERS = ("work", "data", "output", ".omnicrawler")


def user_workspace_reason(package: Path) -> str:
    """版本目录里若已有用户数据，返回可读原因（否则空串）。

    判据是"**目录存在且非空**"，不看体积下限：哪怕只有一个文件，也说明用户的运行数据
    已经落在这里 ⇒ 删掉就是删用户数据。
    """
    for name in WORKSPACE_MARKERS:
        candidate = package / name
        try:
            if not candidate.is_dir():
                continue
            has_content = any(candidate.iterdir())
        except OSError:
            # 读不了（权限/占用）⇒ 当作"有数据"处理：宁可留着，也不要赌
            return f"{name}/（无法读取，保守视为有数据）"
        if has_content:
            return f"{name}/ 非空"
    return ""


def holds_user_workspace(package: Path) -> bool:
    return bool(user_workspace_reason(package))

#: 待清理清单（相对路径）。改名让位后的旧文件若**当场删不掉**（正被占用），
#: 就登记在这里，等下一次运行（＝"下次启动"）再删。
PENDING_FILENAME = "pending-cleanup.json"


class UpgradeManager:
    def __init__(
        self,
        app_root: Path,
        *,
        trusted_public_key: bytes | None = None,
        install_root: Path | None = None,
    ) -> None:
        # trusted_public_key 只有 stage/stage_members（取包）需要；清理类操作传 None 即可。
        self.app_root = app_root.resolve()
        # ★ 布局（`versions/` 与 `current.txt`）认**安装根**，载荷落点认**运行目录**。
        #   版本化布局下运行的是 `<安装根>/versions/<v>/` 里的程序，两者不同：
        #   若照旧用 app_root 推"布局"，就会在 `<v>/` 里再套一层 `versions/`（实测可复现），
        #   指针也读不到真正的那个 ⇒ `--to-versions`/`cleanup` 全线错位。
        self.install_root = (
            Path(install_root).resolve() if install_root is not None else self.app_root
        )
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
        """版本目录集合的根 —— 在**安装根**下（不是运行目录，见 `__init__`）。"""
        return self.install_root / VERSIONS_DIRNAME

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
        """返回"**可清理**的旧版本目录"（绝对路径 + 体积字节）。

        规则：布局 B 下，除 `current.txt` 指向的那份外全部算旧版本；
        就地布局（无指针）下若存在 versions/ 目录，则其中**全部**目录都算残留
        （没有指针就说明当前生效的不是它们）。

        ★★ **但含用户数据的版本目录一律排除**（`protected_version_dirs()`），原因是一条实测过的
        数据丢失路径：2026-09-30 之前 `portable_data_root()` 返回 `application_dir()`，于是
        通过启动器跑 `versions/<v>/` 里的程序时，**用户新建的 `work/`、`data/` 就写在那个版本
        目录里**；而这里的规则是"除当前版外全部删" ⇒ 下一次 `cleanup --yes` 会把它们
        `rmtree` 掉。宁可少回收一些磁盘，也不能删用户数据 —— 所以这里**只返回安全的那些**，
        被保护的原样列给调用方（可见，但不删）。
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
            if holds_user_workspace(package):
                continue
            size = sum(f.stat().st_size for f in package.rglob("*") if f.is_file())
            stale.append((package, size))
        return stale

    def protected_version_dirs(self) -> list[tuple[Path, str]]:
        """含用户数据的版本目录（**不删**，只报告）：``(路径, 原因)``。"""
        versions = self.versions_root
        if not versions.is_dir():
            return []
        pointed = self.current_pointed_version()
        protected: list[tuple[Path, str]] = []
        for package in sorted(versions.iterdir()):
            if not package.is_dir() or (pointed and package.name == pointed):
                continue
            reason = user_workspace_reason(package)
            if reason:
                protected.append((package, reason))
        return protected

    def remove_stale_version_dirs(self) -> dict[str, Any]:
        """删除全部**可清理**的旧版本目录；删不掉（文件被占用）的原样保留并如实返回。

        ★ 含用户数据的目录不在 `stale_version_dirs()` 里（见其 docstring）⇒ 这里天然删不到它们；
        它们由 `protected_version_dirs()` 如实报出（`protected` 字段），不静默。
        """
        freed = 0
        failed: list[str] = []
        removed: list[str] = []
        for package, size in self.stale_version_dirs():
            try:
                shutil.rmtree(package)
                freed += size
                removed.append(package.name)
            except OSError:
                failed.append(self._layout_relative(package))
        protected = self.protected_version_dirs()
        return {
            "removed": removed,
            "failed": failed,
            "freed_bytes": freed,
            "protected": [
                {"path": self._layout_relative(package), "reason": reason}
                for package, reason in protected
            ],
        }

    def _layout_relative(self, path: Path) -> str:
        """把版本目录表达成相对**安装根**的路径（报告用，绝不因计算失败而抛）。"""
        try:
            return path.relative_to(self.install_root).as_posix()
        except ValueError:
            return path.as_posix()


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
