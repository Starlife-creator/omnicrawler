from __future__ import annotations

import hashlib
import secrets
import shutil
from pathlib import Path
from typing import Any

from ..core.config import AppConfig, load_config
from ..core.models import CrawlRequest
from ..core.utils import utcnow
from ..fetching import session_crypto
from ..fetching.authentication import session_scope
from ..fetching.profile_registry import ProfileRegistry
from ..fetching.session import get_cookie_session, invalidate_cookie_sessions
from ..fetching.session_bridge import select_bridgeable_cookies
from ..fetching.session_lease import session_lease
from ..fetching.session_state import context_key_for_request, require_session_state_path
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
        pending = int(frontier.get("pending", 0))
        incomplete_runs = state.rows(
            "SELECT run_id, status FROM runs WHERE status IN ('running', 'paused', 'retrying') ORDER BY started_at"
        )
        sessions = self.config.workspace / "sessions"
        session_files = [path for path in sessions.iterdir() if path.is_file()] if sessions.is_dir() else []
        return {
            "continue": {
                # 2026-09-11：`pending` 也算「可继续」。取消/停止是**正常终态**，
                # 但往往留下大量待处理请求；此前只看「中断中的运行 + 处理中请求」，
                # 于是取消后的任务被判为「无可继续」，recommended_action 会去推荐
                # 别的动作（如 reprocess）——把用户引到错误的操作上。
                "available": bool(incomplete_runs or in_progress or pending),
                "affected": {
                    "runs": len(incomplete_runs),
                    "frontier_requests": in_progress,
                    "pending_requests": pending,
                    "run_ids": [str(row["run_id"]) for row in incomplete_runs[:20]],
                },
                "effect": (
                    "将中断中的运行标记为可恢复、把处理中请求安全退回待处理；"
                    "若仍有待处理请求，可用 resume 继续。"
                    "已完成记录、原始档案和导出不会被删除。"
                ),
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
        return {"recovered_runs": recovered, "next_command": f"omnicrawler resume -c {self.config.path}"}

    def retry_failed(self, limit: int | None = None) -> dict[str, Any]:
        if not self.database.is_file():
            return {"retried": 0, "message": "没有断点数据库。"}
        with StateStore(self.database) as state:
            count = state.retry_failed(limit)
        return {"retried": count, "next_command": f"omnicrawler resume -c {self.config.path}"}

    def reset_login(self) -> dict[str, Any]:
        with session_lease(self.config.workspace):
            invalidate_cookie_sessions(self.config.workspace)
            return self._reset_login_stopped()

    def retry_after_login(self, snapshot: Path, *, hosts: tuple[str, ...]) -> dict[str, Any]:
        """Restore only matching authentication failures after a persisted login.

        The snapshot is verified locally and bound to this workspace. Reusing the
        same snapshot cannot repeatedly retry a still-failing authentication check.
        The caller selects browser-only or HTTP bridging before invoking this step.
        """
        with session_lease(self.config.workspace):
            root = (self.config.workspace / "sessions").resolve()
            snapshot = snapshot.resolve()
            if snapshot.parent != root or not snapshot.name.endswith(".playwright.json"):
                raise ValueError("登录快照须来自当前工作区会话目录")
            expected = require_session_state_path(self.config, context_key_for_request(self.config, CrawlRequest("https://example.invalid/")))
            if snapshot != expected.resolve():
                raise ValueError("登录快照账号或代理身份与当前任务不一致")
            captured = session_crypto.load_storage_state(snapshot)
            if not isinstance(captured, dict) or not captured.get("cookies"):
                raise ValueError("登录快照缺少 Cookie，不能据此恢复认证失败")
            generation = hashlib.sha256(snapshot.read_bytes()).hexdigest()
            invalidate_cookie_sessions(self.config.workspace)
            if not self.database.is_file():
                return {"retried": 0, "generation": generation}
            count = 0
            with StateStore(self.database) as state:
                for host in dict.fromkeys(hosts):
                    matched, _foreign = select_bridgeable_cookies(captured["cookies"], hosts=[host])
                    if not matched:
                        continue
                    scope = session_scope(self.config, CrawlRequest(f"https://{host}/"))
                    count += state.retry_authentication(state.authentication_failures(scope), scope=scope, generation=generation)
            return {"retried": count, "generation": generation,
                    "next_command": f"omnicrawler resume -c {self.config.path}"}

    def _reset_login_stopped(self) -> dict[str, Any]:
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

    def logout_current_session(self) -> dict[str, Any]:
        """Clear this task's active local login state, retaining reversible quarantine."""
        with session_lease(self.config.workspace):
            workspace = self.config.workspace.resolve()
            request = CrawlRequest("https://example.invalid/")
            snapshot = require_session_state_path(self.config, context_key_for_request(self.config, request))
            http_path = get_cookie_session(self.config).path
            account = str(self.config.section("session").get("name", "default"))
            profiles_root = workspace / "browser_profiles"
            profiles = ProfileRegistry(profiles_root).list_all() if profiles_root.is_dir() else []
            paths = [snapshot, *([http_path] if http_path else [])]
            paths.extend(profile.root for profile in profiles if profile.scope.split("|")[1:2] == [account])
            paths = [path for path in paths if path.exists()]
            for path in paths:
                if workspace not in path.resolve().parents or path.is_symlink():
                    raise ValueError("登录资源超出当前工作区，拒绝清除")
            quarantine = workspace / "recovery" / f"logout-{secrets.token_hex(8)}"
            if workspace not in quarantine.resolve().parents:
                raise ValueError("登录隔离目录超出工作区")
            invalidate_cookie_sessions(workspace)
            moved: list[tuple[Path, Path]] = []
            try:
                for path in paths:
                    destination = quarantine / path.relative_to(workspace)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    path.replace(destination)
                    moved.append((path, destination))
            except OSError:
                for original, destination in reversed(moved):
                    destination.replace(original)
                raise
            return {"moved": len(moved), "quarantine": str(quarantine) if moved else None,
                    "message": "本任务本地登录态已清除；隔离文件可恢复，目标站点在线会话未执行注销。"}

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
