"""草稿自动保存与恢复模块。

提供配置的定期自动保存和应用崩溃后的草稿恢复功能。
"""

from __future__ import annotations

import copy
import logging
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from ..i18n import _
from .config_model import CrawlConfig
from .config_serializer import load_yaml, save_yaml

AUTOSAVE_INTERVAL_MS = 60_000  # 60 秒
AUTOSAVE_DIR = "configs"
AUTOSAVE_PREFIX = ".autosave_"


LOGGER = logging.getLogger(__name__)


class AutosaveManager(QObject):
    """草稿自动保存管理器。

    定期将当前配置保存为草稿文件，并在应用恢复时检测残余草稿。
    S3.1.25：写盘移入后台线程；失败经 save_failed 信号可见；间隔可配置。

    Signals:
        draft_found: 检测到未保存草稿时发射，携带草稿文件路径。
        save_failed: 自动保存失败时发射，携带错误消息。
    """

    draft_found = Signal(str)  # 草稿文件路径
    save_failed = Signal(str)  # 保存失败消息

    def __init__(
        self, project_root: Path, parent: QObject | None = None,
        *, interval_ms: int = AUTOSAVE_INTERVAL_MS,
    ) -> None:
        """初始化草稿管理器。

        Args:
            project_root: 项目根目录（configs/ 目录将基于此路径）。
            parent: Qt 父对象。
            interval_ms: 自动保存间隔（毫秒），默认 60s，可配置。
        """
        super().__init__(parent)
        self._project_root = Path(project_root)
        self._config: CrawlConfig | None = None
        self._draft_path: Path | None = None
        self._last_save_time: float = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_timer)
        self._timer.setInterval(max(1_000, int(interval_ms)))

    @property
    def autosave_dir(self) -> Path:
        """自动保存目录路径。"""
        return self._project_root / AUTOSAVE_DIR

    def set_config(self, config: CrawlConfig) -> None:
        """设置当前活跃配置并启动定时保存。

        Args:
            config: 当前爬虫配置对象。
        """
        self._config = config
        self._draft_path = self._get_draft_path(config)
        self._timer.start()

    def stop(self) -> None:
        """停止定时保存。"""
        self._timer.stop()

    def save_now(self) -> bool:
        """立即执行一次草稿保存（S3.1.25：后台线程写盘，主线程不卡顿）。

        Returns:
            保存任务已提交返回 True，否则返回 False。
            写盘失败经 save_failed 信号提示（不再静默）。
        """
        if self._config is None or self._draft_path is None:
            return False
        # FINAL-W3：主线程先深拷贝快照再交后台线程。原实现把活跃配置对象
        # 直接传进写盘线程，与主线程的并发修改竞争，可能序列化出撕裂草稿。
        snapshot = copy.deepcopy(self._config)
        draft_path = self._draft_path

        def _write() -> None:
            try:
                save_yaml(snapshot, draft_path)
            except Exception as exc:  # noqa: BLE001 - 写盘失败提示不崩溃
                self.save_failed.emit(_(f"自动保存失败: {exc}"))

        threading.Thread(target=_write, name="omnicrawler-autosave", daemon=True).start()
        self._last_save_time = time.time()
        return True

    def delete_draft(self) -> None:
        """删除当前草稿文件。"""
        if self._draft_path and self._draft_path.is_file():
            try:
                self._draft_path.unlink()
            except OSError:
                pass

    def check_for_drafts(self) -> list[Path]:
        """检测是否存在残余草稿文件。

        Returns:
            草稿文件路径列表。
        """
        drafts: list[Path] = []
        autosave_dir = self.autosave_dir
        if not autosave_dir.is_dir():
            return drafts
        for item in autosave_dir.iterdir():
            if item.is_file() and item.name.startswith(AUTOSAVE_PREFIX):
                drafts.append(item)
        return drafts

    def load_draft(self, draft_path: Path) -> CrawlConfig | None:
        """加载草稿文件。

        Args:
            draft_path: 草稿文件路径。

        Returns:
            加载成功返回 CrawlConfig，失败返回 None。

        ★ 2026-11-25：失败时**必须留痕**。

        此前这里 `except Exception: return None` 什么都不记，而调用方
        （`main.py::_on_draft_found`）在用户点了「恢复」之后只做
        `if config:` —— 返回 None 时**界面毫无反应**：没有错误、没有提示、
        对话框关掉就像什么都没发生。用户会以为「点了恢复但草稿没回来」，
        却拿不到任何可排查的信息。

        实测两种情形都返回 None 且静默：

        * 草稿文件不存在（并发清理 / 手动删除）；
        * YAML 解析失败（草稿被截断 —— 自动保存写一半时断电就会这样）。

        现在把异常交给调用方判断：返回 None 仍表示"没加载成功"，
        但**原因已写进日志**，用户反馈支持时能直接看到是缺文件还是坏 YAML。
        """
        try:
            config = load_yaml(draft_path)
        except Exception as exc:  # noqa: BLE001 - 草稿损坏不该让 GUI 崩
            LOGGER.warning(_("草稿加载失败（%s）：%s"), draft_path, exc)
            return None
        self._config = config
        self._draft_path = draft_path
        return config

    def scan_and_emit(self) -> None:
        """扫描残余草稿并发射信号。"""
        drafts = self.check_for_drafts()
        for draft in drafts:
            # 跳过空文件
            try:
                if draft.stat().st_size == 0:
                    draft.unlink()
                    continue
            except OSError:
                continue
            self.draft_found.emit(str(draft))

    def _get_draft_path(self, config: CrawlConfig) -> Path:
        """根据配置生成草稿文件路径。"""
        return self.autosave_dir / f"{AUTOSAVE_PREFIX}{config.task_id}.yaml"

    def _on_timer(self) -> None:
        """定时器回调：执行自动保存。"""
        # 如果 60 秒内未保存过则执行
        if time.time() - self._last_save_time >= AUTOSAVE_INTERVAL_MS / 1000:
            self.save_now()
