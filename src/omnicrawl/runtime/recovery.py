from __future__ import annotations

import secrets
import shutil
from pathlib import Path
from typing import Any

from ..core.config import AppConfig, load_config
from ..core.utils import utcnow
from ..state import StateStore
from .run_control import RunControl


class RecoveryCenter:
    """Safe, scriptable recovery operations shared by CLI and future desktop UI."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.database = config.workspace / "state.sqlite3"

    def overview(self) -> dict[str, Any]:
        if not self.database.is_file():
            return {
                "status": "not_started",
                "database": str(self.database),
                "actions": self.actions(),
                "action_previews": self._empty_previews(),
                "recommended_action": "start_new",
            }
        with StateStore(self.database) as state:
            totals = state.stats()
            previews = self._action_previews(state, totals)
            return {
                "status": "ready",
                "database": str(self.database),
                "latest_run": state.latest_run(),
                "totals": totals,
                "actions": self.actions(),
                "action_previews": previews,
                "recommended_action": self._recommended_action(previews),
            }

    @staticmethod
    def actions() -> list[str]:
        return ["continue", "retry-failed", "relogin", "reprocess", "rollback-config"]

    @staticmethod
    def _empty_previews() -> dict[str, dict[str, Any]]:
        return {
            "continue": {
                "available": False,
                "affected": {"runs": 0, "frontier_requests": 0},
                "effect": "没有断点数据库；此操作不会创建或删除结果。",
            },
            "retry-failed": {
                "available": False,
                "affected": {"failed_requests": 0},
                "effect": "没有断点数据库；此操作不会创建或删除结果。",
            },
            "relogin": {
                "available": False,
                "affected": {"session_files": 0},
                "effect": "没有持久化登录会话可隔离。",
            },
            "reprocess": {
                "available": False,
                "affected": {"records": 0, "artifacts": 0},
                "effect": "没有断点数据库；无法从已有原始档案重处理。",
            },
            "rollback-config": {
                "available": True,
                "affected": {"config_files": 1},
                "effect": "需要提供已验证的配置备份；当前配置会先被保留为时间戳副本。",
            },
        }

    def _action_previews(self, state: StateStore, totals: dict[str, Any]) -> dict[str, dict[str, Any]]:
        frontier = totals.get("frontier", {})
        in_progress = int(frontier.get("in_progress", 0))
        failed = int(frontier.get("failed", 0))
        incomplete_runs = state.rows(
            "SELECT run_id, status FROM runs WHERE status IN ('running', 'paused', 'retrying') ORDER BY started_at"
        )
        sessions = self.config.workspace / "sessions"
        session_files = [path for path in sessions.iterdir() if path.is_file()] if sessions.is_dir() else []
        return {
            "continue": {
                "available": bool(incomplete_runs or in_progress),
                "affected": {
                    "runs": len(incomplete_runs),
                    "frontier_requests": in_progress,
                    "run_ids": [str(row["run_id"]) for row in incomplete_runs[:20]],
                },
                "effect": "将中断中的运行标记为可恢复，并把处理中请求安全退回待处理；已完成记录、原始档案和导出不会被删除。",
            },
            "retry-failed": {
                "available": bool(failed),
                "affected": {"failed_requests": failed},
                "effect": "只把失败请求放回待处理并清除其错误计数；不会重置已完成请求或删除输出。",
            },
            "relogin": {
                "available": bool(session_files),
                "affected": {"session_files": len(session_files)},
                "effect": "把现有会话移动到任务内隔离目录；下次运行要求重新登录，可手工恢复隔离文件。",
            },
            "reprocess": {
                "available": bool(totals.get("records") or totals.get("artifacts")),
                "affected": {"records": int(totals.get("records", 0)), "artifacts": int(totals.get("artifacts", 0))},
                "effect": "从本地原始档案重跑提取、质量检查和导出，不重新下载网页。",
            },
            "rollback-config": {
                "available": True,
                "affected": {"config_files": 1},
                "effect": "需要提供已验证的配置备份；当前配置会先被保留为时间戳副本。",
            },
        }

    @staticmethod
    def _recommended_action(previews: dict[str, dict[str, Any]]) -> str:
        for action in ("continue", "retry-failed", "relogin", "reprocess"):
            if previews[action]["available"]:
                return action
        return "inspect_logs"

    def continue_incomplete(self) -> dict[str, Any]:
        self.config.workspace.mkdir(parents=True, exist_ok=True)
        RunControl(self.config.workspace).resume()
        if not self.database.is_file():
            return {"recovered_runs": [], "message": "没有可恢复的运行；可直接启动新任务。"}
        with StateStore(self.database) as state:
            recovered = state.recover_incomplete_runs()
        return {"recovered_runs": recovered, "next_command": f"omnicrawl resume -c {self.config.path}"}

    def retry_failed(self, limit: int | None = None) -> dict[str, Any]:
        if not self.database.is_file():
            return {"retried": 0, "message": "没有断点数据库。"}
        with StateStore(self.database) as state:
            count = state.retry_failed(limit)
        return {"retried": count, "next_command": f"omnicrawl resume -c {self.config.path}"}

    def reset_login(self) -> dict[str, Any]:
        sessions = (self.config.workspace / "sessions").resolve()
        workspace = self.config.workspace.resolve()
        if sessions.parent != workspace:
            raise ValueError("会话目录不在任务工作区内")
        files = [path for path in sessions.iterdir() if path.is_file()] if sessions.is_dir() else []
        if not files:
            return {"moved": 0, "quarantine": None, "message": "没有持久化登录会话。"}
        stamp = utcnow().replace(":", "-").replace("+", "_")
        # S2.5.20：同秒两次 reset 不再 FileExistsError——随机后缀 + exist_ok
        stamp += f"-{secrets.token_hex(3)}"
        quarantine = self.config.workspace / "recovery" / f"sessions-{stamp}"
        quarantine.mkdir(parents=True, exist_ok=True)
        for path in files:
            shutil.move(str(path), str(quarantine / path.name))
        return {
            "moved": len(files),
            "quarantine": str(quarantine),
            "message": "旧会话已隔离；下次运行会重新登录，可从隔离目录恢复。",
        }

    def rollback_config(self, backup: Path) -> dict[str, Any]:
        backup = backup.expanduser().resolve()
        if not backup.is_file():
            raise FileNotFoundError(f"配置备份不存在: {backup}")
        load_config(backup)
        stamp = utcnow().replace(":", "-").replace("+", "_")
        preserved = self.config.path.with_name(f"{self.config.path.name}.before-rollback-{stamp}")
        shutil.copy2(self.config.path, preserved)
        shutil.copy2(backup, self.config.path)
        load_config(self.config.path)
        return {
            "restored_from": str(backup),
            "config": str(self.config.path),
            "previous_config": str(preserved),
        }
