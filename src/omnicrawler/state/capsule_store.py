"""证据胶囊：append-only 单真源日志（阶段 0 H2）。

设计决策（H2）：
- 胶囊的唯一真源是「按 run_id 分片的追加日志文件」，决策树/时间线由
  parent_id 现场构建，**不建 decision_graph 表**（避免双存储不一致）。
- 行格式：每行一个 JSON 对象；坏行读取时跳过（追加日志允许容错）。
- 轮转：单个 run 超过 max_lines 或超过 keep_days → gzip 压缩到 archive/。

胶囊由 pipeline（OMNICRAWL_CAPSULE_ENABLED=true 时）写入，见批 B-1。
"""

from __future__ import annotations

import gzip
import json
import os
import re
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..core.utils import utcnow

# B04-001：run_id 参与文件路径构造，必须为纯安全字符（防路径穿越）。
# 与 StateStore 的 uuid4().hex 生成约定对齐，同时兼容历史自定义 run_id。
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")

_CAPSULE_KEEP_DAYS = 7
_CAPSULE_MAX_LINES = 10_000


@dataclass(slots=True)
class Capsule:
    """一条提取动作证据胶囊。"""

    run_id: str
    action_type: str  # extract_field | exception（http 不生成胶囊，用 raw 归档替代）
    capsule_id: str = ""  # 缺省由 append() 自动生成
    action_name: str = ""
    parent_id: str | None = None
    timestamp: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    code_location: str = ""
    environment: dict[str, str] = field(default_factory=dict)

    def to_line(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, default=str)


class CapsuleStore:
    """胶囊日志读写与轮转。线程安全通过 append 的 O_APPEND 原子性 + 调用方串行保证。"""

    def __init__(
        self,
        base_dir: Path,
        *,
        keep_days: int = _CAPSULE_KEEP_DAYS,
        max_lines: int = _CAPSULE_MAX_LINES,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.keep_days = keep_days
        self.max_lines = max_lines

    # ── 写 ──────────────────────────────────────────────
    def append(self, run_id: str, capsule: Capsule) -> Path:
        """原子追加一条胶囊（单行写入）；持久化屏障移至 rotate（FINAL-D5）。

        胶囊是诊断/重放证据而非事务数据：逐条 fsync 在高频抽取下造成
        显著 I/O 放大（每条 2 次）。POSIX 追加写的行原子性足以保证读取侧
        不见半行；崩溃窗口内最后若干条丢失可接受。rotate() 落盘前统一 fsync。
        """
        if not capsule.timestamp:
            capsule.timestamp = utcnow()
        if not capsule.capsule_id:
            capsule.capsule_id = uuid.uuid4().hex
        path = self._run_file(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(capsule.to_line() + "\n")
            handle.flush()
        return path

    # ── 读 ──────────────────────────────────────────────
    def read(self, run_id: str) -> list[Capsule]:
        """按写入顺序读取一个 run 的全部胶囊；坏行跳过。"""
        path = self._run_file(run_id)
        if not path.is_file():
            return []
        capsules: list[Capsule] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                capsules.append(Capsule(**json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                continue  # 坏行容错
        return capsules

    def count(self, run_id: str) -> int:
        path = self._run_file(run_id)
        if not path.is_file():
            return 0
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())

    # ── 轮转 ────────────────────────────────────────────
    def rotate(self) -> int:
        """行数超限或超时的 run 日志压缩到 archive/ 并删除原文件。

        Returns:
            被压缩/清理的日志文件数。
        """
        archive_dir = self.base_dir / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        now = time.time()
        rotated = 0
        # FINAL-D5：压缩前对活跃日志统一 fsync——把 append 侧省下的持久化
        # 屏障在低频路径补上，归档内容与已刷写数据一致。
        for path in sorted(self.base_dir.glob("*.log")):
            if path.is_file():
                try:
                    fd = os.open(path, os.O_RDONLY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                except OSError:
                    pass
        for path in sorted(self.base_dir.glob("*.log")):
            if not path.is_file():
                continue
            lines = sum(1 for _ in path.open("r", encoding="utf-8"))
            expired = False
            if lines > self.max_lines:
                expired = True
            else:
                try:
                    age = now - path.stat().st_mtime
                except OSError:
                    continue
                expired = age > self.keep_days * 86400
            if not expired:
                continue
            target = archive_dir / f"{path.stem}.log.gz"
            with path.open("rb") as src, gzip.open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            path.unlink(missing_ok=True)
            rotated += 1
        return rotated

    def _run_file(self, run_id: str) -> Path:
        # B04-001：run_id 必须为纯安全字符，拒绝 / \ .. 等路径穿越成分。
        if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
            raise ValueError(f"run_id 含非法字符，禁止参与文件路径构造: {run_id!r}")
        return self.base_dir / f"{run_id}.log"
