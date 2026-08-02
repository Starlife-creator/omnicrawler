"""草稿自动保存与恢复模块。

提供配置的定期自动保存和应用崩溃后的草稿恢复功能。
"""

from __future__ import annotations

import time
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .config_model import CrawlConfig
from .config_serializer import load_yaml, save_yaml

AUTOSAVE_INTERVAL_MS = 60_000  # 60 秒
AUTOSAVE_DIR = "configs"
AUTOSAVE_PREFIX = ".autosave_"


class AutosaveManager(QObject):
    """草稿自动保存管理器。

    定期将当前配置保存为草稿文件，并在应用恢复时检测残余草稿。

    Signals:
        draft_found: 检测到未保存草稿时发射，携带草稿文件路径。
    """

    draft_found = pyqtSignal(str)  # 草稿文件路径

    def __init__(self, project_root: Path, parent: QObject | None = None) -> None:
        """初始化草稿管理器。

        Args:
            project_root: 项目根目录（configs/ 目录将基于此路径）。
            parent: Qt 父对象。
        """
        super().__init__(parent)
        self._project_root = Path(project_root)
        self._config: CrawlConfig | None = None
        self._draft_path: Path | None = None
        self._last_save_time: float = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_timer)
        self._timer.setInterval(AUTOSAVE_INTERVAL_MS)

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
        """立即执行一次草稿保存。

        Returns:
            保存成功返回 True，否则返回 False。
        """
        if self._config is None or self._draft_path is None:
            return False
        try:
            save_yaml(self._config, self._draft_path)
            self._last_save_time = time.time()
            return True
        except Exception:
            return False

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
        """
        try:
            config = load_yaml(draft_path)
            self._config = config
            self._draft_path = draft_path
            return config
        except Exception:
            return None

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
