from __future__ import annotations

import shutil
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from ...core.utils import utcnow
from .log_parser import LogParser
from ...runtime.execution_backend import LocalWorkerBackend
from ..core.config_model import CrawlConfig
from ..core.config_serializer import save_yaml
from ..core.validator import validate_full_config
from ..i18n import _


def _derive_worker_command(omnicrawl_path: str) -> list[str] | None:
    """从用户指定的 CLI 路径推导配套 worker 可执行。

    F35：GUI「手动指定路径」所选可执行应对实际 worker 运行生效；
    仅当路径是真实文件且同目录存在 omnicrawl-worker.exe 时采用，
    否则回退 backend 自动探测（sys.executable 或冻结同目录 worker）。
    """
    if not omnicrawl_path or omnicrawl_path == "omnicrawl":
        return None
    candidate = Path(omnicrawl_path).expanduser()
    if not candidate.is_file():
        return None
    worker = candidate.parent / "omnicrawl-worker.exe"
    if worker.is_file():
        return [str(worker)]
    return None


class WorkerTaskRunner(QObject):
    """Qt adapter for the reconnectable LocalWorkerBackend."""

    log_line = pyqtSignal(str, str)
    progress = pyqtSignal(int, str)
    state_changed = pyqtSignal(str)
    task_finished = pyqtSignal(str, int)

    def __init__(
        self,
        omnicrawl_path: str = "omnicrawl",
        project_root: Path | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._omnicrawl_path = omnicrawl_path
        self._project_root = Path(project_root) if project_root else Path.cwd()
        self._backend = LocalWorkerBackend(worker_command=_derive_worker_command(omnicrawl_path))
        self._state = "idle"
        self._current_task_id = ""
        self._yaml_path: Path | None = None
        # S3.3.1：日志解析器——增量读取 worker 日志，解析 PROGRESS 行驱动进度条
        self._parser = LogParser(on_progress=self.progress.emit)
        self._log_path: Path | None = None
        self._log_offset = 0
        self._poller = QTimer(self)
        self._poller.setInterval(750)
        self._poller.timeout.connect(self._poll)

    @property
    def state(self) -> str:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._state in {"running", "paused", "stopping", "retrying"}

    @property
    def config_path(self) -> Path | None:
        return self._yaml_path

    def set_omnicrawl_path(self, command_path: str) -> None:
        if self.is_running:
            raise RuntimeError(_("任务运行期间不能切换 omnicrawl 命令"))
        self._omnicrawl_path = command_path
        # 无论是否推导出配套 worker，都重建 backend，避免残留旧路径的 worker 命令
        self._backend = LocalWorkerBackend(worker_command=_derive_worker_command(command_path))

    def start(self, config: CrawlConfig, log_level: str = "INFO") -> bool:
        errors, warnings = validate_full_config(config)
        if errors:
            for error in errors:
                self.log_line.emit(error, "error")
            return False
        for warning in warnings:
            self.log_line.emit(warning, "warn")
        if config.has_placeholders():
            self.log_line.emit(_("配置中存在未替换的模板占位符，请先替换后再运行"), "error")
            return False
        usage = shutil.disk_usage(self._project_root)
        if usage.free < 500 * 1024 * 1024:
            self.log_line.emit(_("磁盘剩余空间不足500MB，建议先清理。"), "warn")
        configs = self._project_root / "configs"
        configs.mkdir(parents=True, exist_ok=True)
        timestamp = (config.created_at or utcnow()).replace(":", "-").replace("T", "_")[:19]
        self._yaml_path = configs / f"{config.project_name}_{timestamp}.yaml"
        try:
            save_yaml(config, self._yaml_path)
            result = self._backend.start(self._yaml_path)
        except Exception as exc:
            # S3.1.8：启动失败清理残留配置（backend.start 失败不留孤 YAML）
            if self._yaml_path is not None:
                try:
                    self._yaml_path.unlink(missing_ok=True)
                except OSError:
                    pass
                self._yaml_path = None
            self.log_line.emit(_(f"本地Worker启动失败: {type(exc).__name__}: {exc}"), "error")
            self._set_state("error")
            return False
        self._current_task_id = config.task_id
        self._set_state(str(result.get("status", "running")))
        self.log_line.emit(_("独立本地Worker已启动；关闭或重启GUI不会终止任务。"), "info")
        # S3.3.1：接管 LogParser——增量读取 worker 日志（workspace/logs/local-worker.log）。
        # CrawlConfig.workspace 是字符串，须按 AppConfig 规则相对 project_root 解析，与 worker 端落盘路径一致。
        ws = Path(config.workspace)
        if not ws.is_absolute():
            ws = (self._project_root / ws).resolve()
        self._log_path = ws / "logs" / "local-worker.log"
        self._log_offset = 0
        self._poller.start()
        return True

    def attach(self, session_file: Path) -> bool:
        try:
            result = self._backend.attach(session_file)
        except Exception as exc:
            self.log_line.emit(_(f"无法重新连接Worker: {exc}"), "error")
            return False
        self._set_state(str(result.get("status", "running")))
        self._poller.start()
        self.log_line.emit(_("已重新连接工作区中的本地Worker。"), "info")
        return True

    def pause(self) -> None:
        try:
            self._backend.pause()
            self._set_state("paused")
        except Exception as exc:
            self.log_line.emit(_(f"暂停失败: {exc}"), "error")

    def resume(self) -> None:
        try:
            self._backend.resume()
            self._set_state("running")
        except Exception as exc:
            self.log_line.emit(_(f"继续失败: {exc}"), "error")

    def stop(self) -> None:
        try:
            self._backend.stop()
            self._set_state("stopping")
        except Exception as exc:
            self.log_line.emit(_(f"停止失败: {exc}"), "error")

    def get_pid(self) -> int | None:
        return self._backend.session.pid if self._backend.session else None

    def _poll(self) -> None:
        # S3.3.1：增量读取 worker 日志，逐行过 LogParser（命中 PROGRESS 行经 on_progress emit progress）
        if self._log_path is not None and self._log_path.is_file():
            try:
                with self._log_path.open("r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(self._log_offset)
                    for raw in fh:
                        line = raw.rstrip("\n").rstrip("\r")
                        if line:
                            self._parser.parse_line(line)
                    self._log_offset = fh.tell()
            except OSError:
                pass
        try:
            result = self._backend.status()
        except Exception as exc:
            self._poller.stop()
            self._set_state("error")
            self.log_line.emit(_(f"Worker连接中断，可从会话文件重新连接: {exc}"), "error")
            return
        status = str(result.get("status", "running"))
        if status in {"succeeded", "failed", "cancelled", "partial_success"}:
            self._poller.stop()
            self._log_path = None  # 任务终态，停止增量读日志
            if status == "partial_success":
                self.log_line.emit(_("任务部分成功(存在错误记录)，见详情。"), "warn")
                self._set_state("finished")
                self.task_finished.emit(self._current_task_id, 0)
            elif status == "succeeded":
                records = int(result.get("records", 0))
                if records == 0:
                    self.log_line.emit(
                        _("任务完成但提取 0 条记录——请检查模板匹配或出网拦截。"), "warn"
                    )
                self._set_state("finished")
                self.task_finished.emit(self._current_task_id, 0)
            else:
                self._set_state("error")
                self.task_finished.emit(self._current_task_id, 1)
        elif status != self._state and status in {"running", "paused", "retrying"}:
            self._set_state(status)

    def _set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            self.state_changed.emit(state)
