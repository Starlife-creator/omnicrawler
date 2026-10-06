"""变更检测引擎 — 定时抓取、哈希对比、变化通知。

参考 changedetection.io 的设计理念，提供轻量级的网页变更监控能力。

核心功能:
    - 定义监控规则（URL + CSS 选择器 + 检测条件）
    - 定时抓取并计算内容哈希
    - 对比历史状态，触发变化事件
    - 支持多种检测条件：内容变化、包含关键词、正则匹配、等值比较
    - JSON 序列化/反序列化（持久化监控规则）

用法::

    from omnicrawler.scheduling.change_detector import ChangeDetector, MonitorRule

    detector = ChangeDetector()
    rule = MonitorRule(
        url="https://example.com",
        name="监控示例首页",
        selector=".main-content",
        condition="changed",
        check_interval=3600,
    )
    detector.add_rule(rule)
    events = await detector.check_all()
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from ..core.safe_data import safe_regex_search
from ..core.utils import atomic_write

LOGGER = logging.getLogger(__name__)


# ── 数据模型 ────────────────────────────────────────────────────────

@dataclass
class MonitorRule:
    """单条变更监控规则。

    Attributes:
        rule_id: 唯一标识符（自动生成 UUID4 十六进制）。
        url: 监控目标 URL。
        name: 用户可读的名称。
        selector: CSS 选择器，限定检测范围。空字符串表示整页。
        condition: 检测条件类型。
            - "changed": 内容发生任何变化。
            - "contains:<text>": 内容包含指定文本。
            - "regex:<pattern>": 内容匹配正则表达式。
            - "equals:<value>": 内容精确等于某个值。
        check_interval: 检查间隔（秒），默认 3600。
        enabled: 是否启用。
        notify_methods: 通知方式列表。
        last_hash: 上次检查时的内容哈希（用于变化检测）。
        last_content: 上次检查时的内容文本（用于条件判断）。
        last_checked: 上次检查时间（UTC）。
        created_at: 创建时间。
    """

    url: str
    name: str = ""
    rule_id: str = ""
    selector: str = ""
    condition: str = "changed"
    check_interval: int = 3600
    enabled: bool = True
    notify_methods: list[str] = field(default_factory=lambda: ["desktop"])
    last_hash: str | None = None
    last_content: str | None = None
    last_checked: datetime | None = None
    created_at: datetime | None = None
    consecutive_checks: int = 1
    cooldown_seconds: int = 0
    minimum_absolute_change: float | None = None
    minimum_relative_change: float | None = None
    ignored_selectors: list[str] = field(default_factory=list)
    webhook_url: str = ""
    webhook_token_ref: str = ""
    candidate_hash: str | None = None
    candidate_count: int = 0
    observed_content: str | None = None
    observed_hash: str | None = None
    last_notified: datetime | None = None
    last_notified_content: str | None = None

    def __post_init__(self) -> None:
        import uuid

        if type(self.consecutive_checks) is not int or not 1 <= self.consecutive_checks <= 100:
            raise ValueError("连续确认次数需要在 1 到 100 之间")
        if type(self.cooldown_seconds) is not int or not 0 <= self.cooldown_seconds <= 604800:
            raise ValueError("冷却时间需要在 0 到 604800 秒之间")
        for value in (self.minimum_absolute_change, self.minimum_relative_change):
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                raise ValueError("变化阈值需要是有限非负数字")
        if self.webhook_url:
            parsed = urlparse(self.webhook_url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
                raise ValueError("Webhook 需要不含明文凭据的 HTTP(S) URL")
        if self.webhook_token_ref and not self.webhook_token_ref.startswith("secret://"):
            raise ValueError("Webhook 凭据必须使用 secret:// 引用")
        if not self.rule_id:
            self.rule_id = uuid.uuid4().hex[:12]
        if self.created_at is None:
            self.created_at = datetime.now(tz=UTC)

    def to_dict(self) -> dict[str, Any]:
        """序列化为可 JSON 存储的字典。"""
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat() if self.created_at else None
        d["last_checked"] = self.last_checked.isoformat() if self.last_checked else None
        d["last_notified"] = self.last_notified.isoformat() if self.last_notified else None
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MonitorRule:
        data = dict(data)
        for key in ("created_at", "last_checked", "last_notified"):
            val = data.get(key)
            if isinstance(val, str):
                try:
                    data[key] = datetime.fromisoformat(val)
                except ValueError:
                    data[key] = None
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ChangeEvent:
    """单次变化事件。

    Attributes:
        rule_id: 触发规则的 ID。
        rule_name: 规则名称。
        url: 触发 URL。
        detected_at: 检测到变化的时间。
        previous_hash: 前次内容哈希。
        current_hash: 当前内容哈希。
        previous_content: 前次内容文本。
        current_content: 当前内容文本。
        diff_summary: 人类可读的变化摘要。
    """

    rule_id: str
    rule_name: str
    url: str
    detected_at: datetime
    previous_hash: str | None
    current_hash: str
    previous_content: str | None = None
    current_content: str | None = None
    diff_summary: str = ""
    event_id: str = field(default_factory=lambda: uuid4().hex)
    notification_eligible: bool = True
    notification_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["detected_at"] = self.detected_at.isoformat()
        return d


# ── 引擎 ────────────────────────────────────────────────────────────

class ChangeDetector:
    """变更检测引擎。

    管理监控规则，执行定时检查，对比内容变化并触发通知。

    用法::

        detector = ChangeDetector(data_dir=Path("./monitor_data"))
        detector.add_rule(MonitorRule(url="...", name="...", selector=".content"))
        events = await detector.check_all()
    """

    def __init__(
        self,
        data_dir: Path | None = None,
        on_notify: Callable[[ChangeEvent], None] | None = None,
        *,
        egress: Any = None,
        fetcher: Any = None,
        durable_delivery: bool = False,
    ) -> None:
        self._rules: dict[str, MonitorRule] = {}
        self._history: dict[str, list[ChangeEvent]] = {}
        self._data_dir = data_dir or Path(".omnicrawler_monitor")
        from .monitor_store import MonitorStore

        self._store = MonitorStore(self._data_dir)
        self._durable_delivery = durable_delivery
        self._on_notify = on_notify
        self._running: bool = True
        # 协作式取消（终态，与 pause/resume 的可逆开关不同）：置位后不再发起新抓取。
        # 跨线程只做一次布尔赋值（CPython 下原子），不引入锁以免在事件循环里阻塞。
        self._cancelled: bool = False
        # 网络边界（S4.5 门禁）：注入 EgressBroker 后所有抓取走授权路径
        self._egress = egress or getattr(fetcher, "egress", None)
        self._check_results: dict[str, dict[str, Any]] = {}
        self._fetch_errors: dict[str, str] = {}
        # A3：注入 AsyncFetcher 后复用其连接池/限速/隐身/EgressBroker 审计通道
        self._fetcher = fetcher
        # S3.2.1：基线持久化——每轮重建规则对象也能恢复 last_hash，
        # 消除 GUI 侧 "__baseline__" 哨兵假哈希导致的每轮误报变化
        self._baselines: dict[str, dict[str, Any]] = {}
        self._load_baselines()
        self._baselines.update(self._store.baselines())

    # ── 规则管理 ────────────────────────────────────────────────────

    def add_rule(self, rule: MonitorRule) -> str:
        """添加一条监控规则。返回 rule_id。

        S3.2.1：若磁盘存在该规则基线，恢复 last_hash/last_content，
        使每轮重建规则对象仍能正确比较。
        """
        baseline = self._baselines.get(rule.rule_id)
        if baseline:
            for name in ("last_hash", "last_content", "candidate_hash", "candidate_count", "last_notified_content"):
                if name in baseline:
                    setattr(rule, name, baseline[name])
            if baseline.get("last_notified"):
                rule.last_notified = datetime.fromisoformat(baseline["last_notified"])
        self._rules[rule.rule_id] = rule
        LOGGER.info("添加监控规则: %s (%s)", rule.name, rule.rule_id)
        return rule.rule_id

    def remove_rule(self, rule_id: str) -> bool:
        """删除一条监控规则。返回是否成功。"""
        if rule_id in self._rules:
            del self._rules[rule_id]
            self._history.pop(rule_id, None)
            LOGGER.info("移除监控规则: %s", rule_id)
            return True
        return False

    def get_rule(self, rule_id: str) -> MonitorRule | None:
        return self._rules.get(rule_id)

    def list_rules(self) -> list[MonitorRule]:
        """列出所有规则。"""
        return list(self._rules.values())

    def pause(self) -> None:
        self._running = False

    def resume(self) -> None:
        self._running = True

    def cancel(self) -> None:
        """请求取消：**不再发起新的抓取**（协作式，可跨线程调用）。

        在网络调用**之前**与每条规则**之间**检查。已经发出的请求无法中断
        （urllib / AsyncFetcher 都是阻塞调用），由调用方在关闭路径上等待其收尾。
        """
        self._cancelled = True
        self._running = False

    @property
    def cancelled(self) -> bool:
        """是否已被请求取消。"""
        return self._cancelled

    # ── 内容获取与哈希 ──────────────────────────────────────────────

    @staticmethod
    def _compute_hash(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()

    @staticmethod
    def _extract_content(html: str, selector: str = "") -> str:
        """从 HTML 中提取目标内容。

        如果指定了 CSS 选择器，只提取匹配部分；否则使用全文。
        尝试用 selectolax/lxml/BeautifulSoup 按优先级回退。
        """
        if not selector:
            return html

        # 尝试 selectolax
        try:
            from selectolax.parser import HTMLParser

            tree = HTMLParser(html)
            nodes = tree.css(selector)
            if nodes:
                return "\n".join(node.text(strip=True) for node in nodes if node.text())
            return ""
        except ImportError:
            pass

        # 尝试 lxml
        try:
            from lxml import etree

            tree = etree.HTML(html)
            elements = tree.cssselect(selector)
            if elements:
                parts: list[str] = []
                for el in elements:
                    text = "".join(el.itertext()).strip()
                    if text:
                        parts.append(text)
                return "\n".join(parts)
            return ""
        except Exception:
            pass

        # 尝试 BeautifulSoup
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "html.parser")
            elements = soup.select(selector)
            if elements:
                return "\n".join(el.get_text(strip=True) for el in elements if el.get_text(strip=True))
            return ""
        except Exception:
            pass

        # 回退：返回原始 HTML
        LOGGER.warning("未能加载 DOM 解析器，对选择器 %s 返回全文", selector)
        return html

    async def _fetch_content(self, url: str) -> str | None:
        """获取页面内容。返回 HTML 字符串，失败时返回 None。

        注入 AsyncFetcher 时复用其连接池/限速/EgressBroker 审计通道；
        否则回退 urllib + EgressBroker（S4.5 网络边界门禁）。

        已被请求取消时直接返回 None：**关闭后不得再发起网络 I/O**
        （这是「关闭窗口时有抓取在飞」那类崩溃的入口防线）。
        """
        if self._cancelled:
            LOGGER.info("变更监测已取消，跳过抓取: %s", url)
            return None
        if self._fetcher is not None:
            try:
                from ..core.models import CrawlRequest

                loop = asyncio.get_running_loop()
                # fetch 内部 run_until_complete 自建 loop，不能在 running loop 内直调，
                # 故经 executor 跨线程执行（无 running loop 冲突）
                result = await loop.run_in_executor(
                    None, self._fetcher.fetch, CrawlRequest(url, kind="page")
                )
                if getattr(result, "status", 200) >= 400:
                    raise RuntimeError("监控页面返回 HTTP 错误")
                content_type = result.headers.get("content-type", "")
                charset = "utf-8"
                if "charset=" in content_type:
                    charset = content_type.split("charset=", 1)[-1].split(";")[0].strip()
                return result.body.decode(charset, errors="replace")
            except Exception as exc:  # noqa: BLE001
                self._fetch_errors[url] = type(exc).__name__
                LOGGER.warning("共享抓取器获取监控页面失败: %s", type(exc).__name__)
                return None
        try:
            from ..extraction.extractors import decode_body
            from ..fetching.page_probe import fetch_static_page

            result = await asyncio.to_thread(fetch_static_page, url, egress=self.network_broker(url))
            return decode_body(result)
        except Exception as exc:
            self._fetch_errors[url] = type(exc).__name__
            LOGGER.warning("获取监控页面失败: %s", type(exc).__name__)
            return None

    def network_broker(self, url: str = "") -> Any:
        """Reuse task policy; standalone monitoring scopes secrets to configured targets."""
        if self._egress is None:
            from urllib.parse import urlsplit

            from ..core.config import AppConfig
            from ..fetching.page_probe import build_inspection_config
            from ..security.egress import EgressBroker

            rules = self.list_rules()
            initial = build_inspection_config(url or next((rule.url for rule in rules), "https://example.com"))
            initial.raw["source"]["seeds"] = [rule.url for rule in rules] or [url]
            targets = [rule.webhook_url for rule in rules if rule.enabled and rule.webhook_url]
            initial.raw["egress"]["credential_domains"] = sorted({
                host for target in targets if (host := urlsplit(target).hostname)
            })
            initial.raw["egress"]["credential_purposes"] = ["notification"]
            self._egress = EgressBroker(AppConfig(initial.path, initial.root, initial.raw, self._data_dir))
        return self._egress

    # ── 条件判断 ────────────────────────────────────────────────────

    @staticmethod
    def _check_condition(content: str, condition: str) -> bool:
        """检查内容是否满足条件。

        Supported conditions:
            - "changed": 始终 True（调用方负责哈希对比）。
            - "contains:<text>": 内容包含指定文本。
            - "regex:<pattern>": 内容匹配正则。
            - "equals:<value>": 内容精确等于指定值。
        """
        if condition == "changed":
            return True
        if condition.startswith("contains:"):
            target = condition[len("contains:"):]
            return target in content
        if condition.startswith("regex:"):
            pattern = condition[len("regex:"):]
            # B08-001：统一走 safe_regex_search（防病态正则自 DOS），编译错误/可疑模式返回 None。
            return bool(safe_regex_search(pattern, content, flags=re.DOTALL))
        if condition.startswith("equals:"):
            target = condition[len("equals:"):]
            return content.strip() == target.strip()
        # 未知条件 → 回退为 "changed"
        LOGGER.warning("未知检测条件 %s，回退为 changed", condition)
        return True

    # ── 检查执行 ────────────────────────────────────────────────────

    def _build_diff_summary(self, previous: str | None, current: str) -> str:
        """生成人类可读的变化摘要。"""
        if previous is None:
            return "首次检查，建立基线"

        if previous == current:
            return "内容未变化"

        # 行级差异统计
        prev_lines = previous.splitlines()
        curr_lines = current.splitlines()
        added = len([line for line in curr_lines if line and line not in prev_lines])
        removed = len([line for line in prev_lines if line and line not in curr_lines])

        parts: list[str] = []
        if added > 0:
            parts.append(f"新增 {added} 行")
        if removed > 0:
            parts.append(f"移除 {removed} 行")

        len_diff = len(current) - len(previous)
        if len_diff > 0:
            parts.append(f"增加 {len_diff} 字符")
        elif len_diff < 0:
            parts.append(f"减少 {abs(len_diff)} 字符")

        return " · ".join(parts) if parts else "内容发生变化"

    async def check_rule(self, rule_id: str) -> ChangeEvent | None:
        """检查单条规则。返回 ChangeEvent（有变化时）或 None（无变化）。"""
        rule = self._rules.get(rule_id)
        self._check_results[rule_id] = {"status": "not_checked"}
        if rule is None or not rule.enabled:
            self._check_results[rule_id]["status"] = "disabled"
            return None

        self.retry_notifications()
        now = datetime.now(tz=UTC)

        # 检查间隔
        if rule.last_checked is not None:
            elapsed = (now - rule.last_checked).total_seconds()
            if elapsed < rule.check_interval:
                self._check_results[rule_id]["status"] = "not_due"
                return None

        # 获取页面内容
        html = await self._fetch_content(rule.url)
        if html is None:
            self._check_results[rule_id] = {"status": "cancelled" if self._cancelled else "failed",
                                            "error_type": self._fetch_errors.pop(rule.url, "fetch_failed")}
            return None

        # 提取目标区域
        if rule.ignored_selectors:
            from ..extraction.html_tools import MiniNode, node_markup, parse_html, select_nodes
            tree = parse_html(html)
            for selector in rule.ignored_selectors:
                for node in select_nodes(tree, selector):
                    if isinstance(node, MiniNode):
                        if node.parent is not None and node in node.parent.children:
                            node.parent.children.remove(node)
                    else:
                        node.decompose()
            html = node_markup(tree)
        content = self._extract_content(html, rule.selector)
        rule.observed_content = content
        current_hash = self._compute_hash(content)
        rule.observed_hash = current_hash

        # 首次检查：建立基线
        if rule.last_hash is None:
            rule.last_hash = current_hash
            rule.last_content = content
            rule.last_checked = now
            self._persist_baseline(rule)
            self._check_results[rule_id]["status"] = "baseline"
            return None

        # 无变化
        if current_hash == rule.last_hash:
            rule.candidate_hash, rule.candidate_count = None, 0
            rule.last_checked = now
            self._persist_baseline(rule)
            self._check_results[rule_id]["status"] = "unchanged"
            return None

        # 检查条件
        if not self._check_condition(content, rule.condition):
            rule.last_checked = now
            self._persist_baseline(rule)
            self._check_results[rule_id]["status"] = "condition_not_met"
            return None
        rule.candidate_count = rule.candidate_count + 1 if rule.candidate_hash == current_hash else 1
        rule.candidate_hash = current_hash
        if rule.candidate_count < rule.consecutive_checks:
            rule.last_checked = now
            self._persist_baseline(rule)
            self._check_results[rule_id]["status"] = "awaiting_confirmation"
            return None

        # 变化事件
        event = ChangeEvent(
            rule_id=rule.rule_id,
            rule_name=rule.name,
            url=rule.url,
            detected_at=now,
            previous_hash=rule.last_hash,
            current_hash=current_hash,
            previous_content=rule.last_content,
            current_content=content,
            diff_summary=self._build_diff_summary(rule.last_content or "", content),
        )

        event.notification_reason = self._notification_reason(rule, content, now)
        event.notification_eligible = not event.notification_reason
        if event.notification_eligible:
            rule.last_notified, rule.last_notified_content = now, content
        rule.candidate_hash, rule.candidate_count = None, 0
        # 更新基线
        rule.last_hash = current_hash
        rule.last_content = content
        rule.last_checked = now
        self._persist_baseline(rule, event)

        # 记录历史（S3.2.1 ⑥：每规则历史有界，防内存无限增长）
        history = self._history.setdefault(rule_id, [])
        history.append(event)
        if len(history) > 50:
            del history[: len(history) - 50]

        # 触发通知
        if self._on_notify:
            self.retry_notifications(_event=event)

        self._check_results[rule_id]["status"] = "changed" if event.notification_eligible else "suppressed"
        LOGGER.info("检测到变化: %s — %s", rule.name, event.diff_summary)
        return event

    def check_report(self) -> dict[str, dict[str, Any]]:
        return {key: dict(value) for key, value in self._check_results.items()}

    @staticmethod
    def _notification_reason(rule: MonitorRule, content: str, now: datetime) -> str:
        if rule.cooldown_seconds and rule.last_notified and (now - rule.last_notified).total_seconds() < rule.cooldown_seconds:
            return "cooldown"
        if rule.minimum_absolute_change is not None or rule.minimum_relative_change is not None:
            try:
                before = float(rule.last_notified_content if rule.last_notified_content is not None else rule.last_content or "")
                after = float(content)
            except ValueError:
                return "numeric_value_unavailable"
            if not math.isfinite(before) or not math.isfinite(after):
                return "numeric_value_unavailable"
            delta = abs(after - before)
            if rule.minimum_absolute_change is not None and delta < rule.minimum_absolute_change:
                return "below_absolute_threshold"
            if rule.minimum_relative_change is not None:
                if before == 0:
                    return "relative_baseline_zero"
                if delta / abs(before) < rule.minimum_relative_change:
                    return "below_relative_threshold"
        return ""

    def pending_notifications(self, *, force: bool = False) -> list[ChangeEvent]:
        active = {key for key, rule in self._rules.items() if rule.enabled}
        events = []
        for row in self._store.pending(active, force=force):
            payload = json.loads(row["body_json"])
            payload["detected_at"] = datetime.fromisoformat(payload["detected_at"])
            events.append(ChangeEvent(**payload))
        return events

    def retry_notifications(self, *, force: bool = False, _event: ChangeEvent | None = None) -> None:
        if self._cancelled or not self._running or self._on_notify is None:
            return
        active = {key for key, rule in self._rules.items() if rule.enabled}
        for row in self._store.pending(active, force=force):
            if self._cancelled or not self._running:
                break
            lease = self._store.claim(row["event_id"])
            if not lease:
                continue
            payload = json.loads(row["body_json"])
            payload["detected_at"] = datetime.fromisoformat(payload["detected_at"])
            try:
                self._on_notify(_event if _event is not None and _event.event_id == row["event_id"] else ChangeEvent(**payload))
            except Exception as exc:
                self._store.fail(row["event_id"], lease_token=lease)
                LOGGER.error("通知回调异常: %s", exc)
            else:
                self._store.acknowledge(row["event_id"], lease_token=lease)

    def acknowledge_notification(self, event_id: str) -> None:
        self._store.acknowledge(event_id)

    def delivery_report(self) -> list[dict[str, Any]]:
        return self._store.report()

    async def check_all(self) -> list[ChangeEvent]:
        """检查所有已启用的规则。返回所有变化事件列表。"""
        events: list[ChangeEvent] = []
        for rule_id in list(self._rules):
            # 取消后不再开新规则（在飞的那条由调用方等待收尾）
            if self._cancelled or not self._running:
                break
            try:
                event = await self.check_rule(rule_id)
                if event:
                    events.append(event)
            except Exception as exc:
                LOGGER.error("检查规则 %s 异常: %s", rule_id, exc)
        if self._durable_delivery and not self._cancelled:
            active = {key for key, rule in self._rules.items() if rule.enabled}
            known = {event.event_id for event in events}
            for row in self._store.pending(active):
                if row["event_id"] not in known:
                    payload = json.loads(row["body_json"])
                    payload["detected_at"] = datetime.fromisoformat(payload["detected_at"])
                    events.append(ChangeEvent(**payload))
        return events

    # ── S3.2.1：基线持久化 ─────────────────────────────────────────

    def _baseline_path(self) -> Path:
        return self._data_dir / "baselines.json"

    def _load_baselines(self) -> None:
        try:
            data = json.loads(self._baseline_path().read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._baselines = data
        except (OSError, json.JSONDecodeError):
            self._baselines = {}

    def _persist_baseline(self, rule: MonitorRule, event: ChangeEvent | None = None) -> None:
        """把规则的 last_hash/last_content/last_checked 写入磁盘基线。"""
        from .webhook import webhook_target_id

        previous = self._baselines.get(rule.rule_id)
        self._baselines[rule.rule_id] = {
            "last_hash": rule.last_hash,
            "last_content": rule.last_content,
            "last_checked": rule.last_checked.isoformat() if rule.last_checked else None,
            "candidate_hash": rule.candidate_hash, "candidate_count": rule.candidate_count,
            "observed_content": rule.observed_content, "observed_hash": rule.observed_hash,
            "last_notified": rule.last_notified.isoformat() if rule.last_notified else None,
            "last_notified_content": rule.last_notified_content,
        }
        try:
            self._store.save(rule.rule_id, self._baselines[rule.rule_id],
                             event.to_dict() if event is not None else None,
                             pending=self._on_notify is not None or self._durable_delivery,
                             targets=[webhook_target_id(rule)] if rule.webhook_url else [], expected=previous)
        except Exception:
            restored = previous or {"last_hash": event.previous_hash if event else None,
                                    "last_content": event.previous_content if event else None}
            rule.last_hash = restored.get("last_hash")
            rule.last_content = restored.get("last_content")
            rule.last_checked = None
            rule.candidate_hash = restored.get("candidate_hash")
            rule.candidate_count = restored.get("candidate_count", 0)
            rule.last_notified_content = restored.get("last_notified_content")
            rule.last_notified = datetime.fromisoformat(restored["last_notified"]) if restored.get("last_notified") else None
            if previous is None:
                self._baselines.pop(rule.rule_id, None)
            else:
                self._baselines[rule.rule_id] = previous
            raise
        try:
            self._baseline_path().parent.mkdir(parents=True, exist_ok=True)
            temporary = self._baseline_path().with_suffix(".tmp")
            temporary.write_text(
                json.dumps(self._baselines, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self._baseline_path())
        except OSError as exc:
            LOGGER.warning("变更监控基线写入失败: %s", exc)

    # ── 历史查询 ────────────────────────────────────────────────────

    def get_history(self, rule_id: str) -> list[ChangeEvent]:
        """获取某条规则的历史变化事件。"""
        return list(self._history.get(rule_id, []))

    # ── 序列化 ──────────────────────────────────────────────────────

    def save_rules(self, path: Path | None = None) -> None:
        """将规则持久化为 JSON 文件。"""
        target = path or self._data_dir / "monitor_rules.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        rules_data = [rule.to_dict() for rule in self._rules.values()]
        # 审计（W6.4）：直接 `write_text` 中途中断会留下**半个 JSON** ⇒ 规则加载失败、
        # 变化监测静默失效。改用既有 `atomic_write`（写临时文件 + fsync + 原子替换）。
        atomic_write(target, json.dumps(rules_data, ensure_ascii=False, indent=2).encode("utf-8"))
        LOGGER.info("已保存 %d 条监控规则到 %s", len(rules_data), target)

    def load_rules(self, path: Path | None = None) -> int:
        """从 JSON 文件加载规则。返回加载的规则数量。"""
        source = path or self._data_dir / "monitor_rules.json"
        if not source.exists():
            return 0
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
            count = 0
            for item in data:
                rule = MonitorRule.from_dict(item)
                self._rules[rule.rule_id] = rule
                count += 1
            LOGGER.info("已加载 %d 条监控规则", count)
            return count
        except Exception as exc:
            LOGGER.error("加载监控规则失败: %s", exc)
            return 0
