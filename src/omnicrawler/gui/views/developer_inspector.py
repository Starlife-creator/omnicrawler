"""Developer 检查器（检视层）。

纯前端只读检视，读现有配置与已装插件元数据，不依赖后端：
- 执行计划：数据源 / 爬取 / 网络 / 抽取 / 输出 / 资源概览
- 权限与网络：插件签名策略、Egress 出口策略、AI 隐私开关
- 插件权限审计：扫描 plugins.paths，静态读取每个插件的权限 / 能力 / 兼容性

注：IR 编辑、离线回放需后端持久化支撑，本期未实现（保留占位）。
「运行时间线」已实现——读 workspace/state.sqlite3 的 run_state_events / stage_checkpoints。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...state import StateStore
from ..core.config_model import CrawlConfig
from ..i18n import _

if TYPE_CHECKING:
    from ...plugins.plugin_inspector import PluginInspection

_PLUGIN_INSPECTOR_WARNED = False


def _config_snapshot(config: CrawlConfig) -> dict[str, Any]:
    """把 GUI 的 CrawlConfig 归一为检视用的分节字典（含 passthrough 中的高级配置）。"""
    pt = config.passthrough
    crawl = pt.get("crawl")
    crawl = crawl if isinstance(crawl, dict) else {}
    http = pt.get("http")
    http = http if isinstance(http, dict) else {}
    ai = pt.get("ai")
    ai = ai if isinstance(ai, dict) else {}
    ai_privacy = ai.get("privacy")
    ai_privacy = ai_privacy if isinstance(ai_privacy, dict) else {}
    extract = crawl.get("extract")
    extract_mode = extract.get("mode", "") if isinstance(extract, dict) else ""
    plugins = pt.get("plugins")
    plugins = plugins if isinstance(plugins, dict) else {}
    egress = pt.get("egress")
    egress = egress if isinstance(egress, dict) else {}
    return {
        "project_name": config.project_name,
        "source": {"kind": config.source_kind, "seeds": config.seed_urls},
        "crawl": {
            "strategy": crawl.get("strategy", ""),
            "max_pages": config.max_pages,
            "max_depth": crawl.get("max_depth", ""),
            "concurrency": config.concurrency,
            "same_host": crawl.get("same_host", ""),
        },
        "http": {
            "engine": http.get("engine", ""),
            "respect_robots": config.respect_robots,
            "verify_tls": http.get("verify_tls", ""),
            "robots_fail_closed": http.get("robots_fail_closed", ""),
            "delay_seconds": config.delay,
            "retries": http.get("retries", ""),
        },
        "extract": {"mode": extract_mode},
        "outputs": {k: (k in config.output_formats) for k in ("jsonl", "csv", "xlsx")},
        "resources": {"profile": config.resource_profile},
        "plugins": plugins,
        "egress": egress,
        "ai": {"privacy": ai_privacy},
    }


def _collect_plugins(config: CrawlConfig, project_root: Path) -> list[PluginInspection]:
    """扫描插件目录，静态读取每个插件元数据（可能慢，调用方自行控制频率）。

    插件路径来自 CrawlConfig.passthrough["plugins"]["paths"]，相对路径按 project_root 解析。
    """
    global _PLUGIN_INSPECTOR_WARNED
    try:
        from ...plugins.plugin_inspector import inspect_plugin
    except Exception:  # pragma: no cover - 依赖缺失时不崩界面
        if not _PLUGIN_INSPECTOR_WARNED:
            _PLUGIN_INSPECTOR_WARNED = True
        return []
    found: list[PluginInspection] = []
    plugins_block = config.passthrough.get("plugins")
    plugins_block = plugins_block if isinstance(plugins_block, dict) else {}
    for raw in plugins_block.get("paths", []) or []:
        directory = Path(raw)
        if not directory.is_absolute():
            directory = project_root / directory
        if not directory.is_dir():
            continue
        # 对齐 registry.load_local_plugins 的递归发现约定（plugins/<id>/plugin.py）
        for entry in sorted(directory.rglob("plugin.py")):
            if entry.parent.name == "__pycache__":
                continue
            found.append(inspect_plugin(entry))
    return found


class DeveloperInspector(QWidget):
    """开发者检查器视图（检视层）。"""

    def __init__(self, config: CrawlConfig, project_root: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("developerInspector")
        self.setAccessibleName(_("开发者检查器"))
        self._config = config
        self._project_root = project_root
        self._snap = _config_snapshot(config)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)

        self._plan_tree = self._new_tree([_("项"), _("值")])
        self._policy_tree = self._new_tree([_("项"), _("值")])
        self._plugin_tree = self._new_tree(
            [_("名称"), _("版本"), _("权限"), _("能力"), _("状态")]
        )
        self._tabs.addTab(self._plan_tree, _("执行计划"))
        self._tabs.addTab(self._policy_tree, _("权限与网络"))
        self._tabs.addTab(self._plugin_tree, _("插件权限审计"))

        # D-lite：运行事件时间线（读 workspace/state.sqlite3 的持久化事件）
        self._timeline_combo = QComboBox()
        self._timeline_combo.setMinimumWidth(360)
        self._timeline_combo.currentIndexChanged.connect(self._render_timeline)
        self._timeline_refresh_btn = QPushButton(_("刷新"))
        self._timeline_refresh_btn.clicked.connect(self._build_timeline_options)
        self._timeline_meta = QLabel("")
        self._timeline_meta.setObjectName("muted")
        self._timeline_meta.setWordWrap(True)
        self._timeline_tree = self._new_tree([_("时间"), _("事件"), _("详情")])

        timeline_tab = QWidget()
        tl = QVBoxLayout(timeline_tab)
        row = QHBoxLayout()
        row.addWidget(QLabel(_("运行：")))
        row.addWidget(self._timeline_combo, 1)
        row.addWidget(self._timeline_refresh_btn)
        tl.addLayout(row)
        tl.addWidget(self._timeline_meta)
        tl.addWidget(self._timeline_tree)
        self._tabs.addTab(timeline_tab, _("运行时间线"))

        self.refresh()

    # ------------------------------------------------------------------ #
    @staticmethod
    def _new_tree(headers: list[str]) -> QTreeWidget:
        tree = QTreeWidget()
        tree.setHeaderLabels(headers)
        tree.setAlternatingRowColors(True)
        tree.setColumnCount(len(headers))
        header = tree.header()
        if header is not None:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            header.setStretchLastSection(True)
        return tree

    @staticmethod
    def _fill(tree: QTreeWidget, rows: list[list[str]]) -> None:
        tree.clear()
        for row in rows:
            tree.addTopLevelItem(QTreeWidgetItem([str(cell) for cell in row]))

    @staticmethod
    def _truthy(value) -> str:
        if isinstance(value, bool):
            return _("是") if value else _("否")
        if value in (None, "", [], {}):
            return _("（空）")
        if isinstance(value, (list, tuple)):
            return ", ".join(str(item) for item in value) if value else _("（空）")
        return str(value)

    def refresh(self) -> None:
        """从当前配置重新读取并刷新检视页（配置变更后调用）。"""
        if self._config is None:
            return
        self._build_plan()
        self._build_policy()
        self._build_plugins()
        self._build_timeline_options()

    def _section(self, name: str) -> dict[str, Any]:
        """读检视分节（由 CrawlConfig 归一快照提供，对空配置健壮）。"""
        value = self._snap.get(name)
        return value if isinstance(value, dict) else {}

    # ------------------------------------------------------------------ #
    def _build_plan(self) -> None:
        c = self._config
        rows: list[list[str]] = []
        rows.append([_("项目名"), c.project_name])
        source = self._section("source")
        rows.append([_("数据源类型"), str(source.get("kind", ""))])
        seeds = source.get("seeds", []) or []
        rows.append([_("入口数量"), str(len(seeds))])
        crawl = self._section("crawl")
        rows.append([_("爬取策略"), str(crawl.get("strategy", ""))])
        rows.append([_("最大页数"), str(crawl.get("max_pages", ""))])
        rows.append([_("最大深度"), str(crawl.get("max_depth", ""))])
        rows.append([_("并发数"), str(crawl.get("concurrency", ""))])
        rows.append([_("同源限制"), self._truthy(crawl.get("same_host"))])
        http = self._section("http")
        rows.append([_("HTTP 引擎"), str(http.get("engine", ""))])
        rows.append([_("遵守 robots.txt"), self._truthy(http.get("respect_robots"))])
        rows.append([_("robots 失败封闭"), self._truthy(http.get("robots_fail_closed"))])
        rows.append([_("校验 TLS"), self._truthy(http.get("verify_tls"))])
        rows.append([_("请求延迟(秒)"), str(http.get("delay_seconds", ""))])
        rows.append([_("重试次数"), str(http.get("retries", ""))])
        extract = self._section("extract")
        rows.append([_("抽取模式"), str(extract.get("mode", ""))])
        outputs = self._section("outputs")
        enabled = [k for k in ("jsonl", "csv", "xlsx") if bool(outputs.get(k, False))]
        rows.append([_("导出格式"), self._truthy(enabled)])
        resources = self._section("resources")
        rows.append([_("资源档位"), str(resources.get("profile", ""))])
        self._fill(self._plan_tree, rows)

    def _build_policy(self) -> None:
        c = self._config
        rows: list[list[str]] = []
        plugins = self._section("plugins")
        rows.append([_("插件签名策略"), str(plugins.get("signature_policy", ""))])
        rows.append([_("插件级权限授权"), self._truthy(plugins.get("permission_grants"))])
        enabled_market = plugins.get("enabled_market_plugins")
        rows.append(
            [
                _("已启用市场插件"),
                _("兼容模式（全部已安装）")
                if enabled_market is None
                else ", ".join(str(item) for item in enabled_market) or _("无"),
            ]
        )
        if plugins.get("approved_permissions"):
            rows.append([_("旧版全局权限（待迁移）"), self._truthy(plugins.get("approved_permissions"))])
        rows.append([_("AST 豁免模式"), self._truthy(plugins.get("ast_allowed_patterns"))])
        trust_key = getattr(c, "plugin_trust_public_key", "") or ""
        rows.append([_("信任根公钥"), _("已接线(内置)") if trust_key else _("（未配置）")])
        egress = self._section("egress")
        rows.append([_("— Egress 出口策略 —"), ""])
        rows.append([_("启用"), self._truthy(egress.get("enabled"))])
        rows.append([_("允许协议"), self._truthy(egress.get("allowed_schemes"))])
        rows.append([_("允许端口"), self._truthy(egress.get("allowed_ports"))])
        rows.append([_("允许域名"), self._truthy(egress.get("allowed_domains"))])
        rows.append([_("凭据域名"), self._truthy(egress.get("credential_domains"))])
        rows.append([_("最大请求数"), str(egress.get("maximum_requests", 0))])
        rows.append([_("最大字节数"), str(egress.get("maximum_bytes", 0))])
        rows.append([_("最大并发"), str(egress.get("maximum_concurrency", 0))])
        rows.append([_("最大运行时(秒)"), str(egress.get("maximum_runtime_seconds", 0))])
        rows.append([_("最大花费"), str(egress.get("maximum_cost", 0))])
        rows.append([_("熔断阈值"), str(egress.get("circuit_failure_threshold", 0))])
        rows.append([_("熔断恢复(秒)"), str(egress.get("circuit_recovery_seconds", 0))])
        rows.append([_("审计日志"), self._truthy(egress.get("audit"))])
        rows.append([_("允许未拦截 Selenium"), self._truthy(egress.get("allow_unintercepted_selenium"))])
        rows.append([_("Selenium BiDi 守卫"), self._truthy(egress.get("experimental_selenium_bidi_guard"))])
        privacy = self._section("ai").get("privacy", {}) or {}
        rows.append([_("— AI 隐私 —"), ""])
        rows.append([_("允许页面文本"), self._truthy(privacy.get("allow_page_text"))])
        rows.append([_("允许 PDF 内容"), self._truthy(privacy.get("allow_pdf_content"))])
        rows.append([_("允许截图"), self._truthy(privacy.get("allow_screenshots"))])
        rows.append([_("允许 Cookie"), self._truthy(privacy.get("allow_cookies"))])
        self._fill(self._policy_tree, rows)

    def _build_plugins(self) -> None:
        rows: list[list[str]] = []
        try:
            inspections = _collect_plugins(self._config, self._project_root)
        except Exception:  # pragma: no cover - 极端降级
            inspections = []
        if not inspections:
            rows.append([_("（未扫描到插件）"), "", "", "", ""])
        for insp in inspections:
            if insp.errors:
                status = _("不兼容: ") + "; ".join(insp.errors)
            elif insp.compatible:
                status = _("兼容")
            else:
                status = _("未知")
            rows.append([
                insp.name,
                insp.version,
                self._truthy(insp.permissions),
                self._truthy(insp.capabilities),
                status,
            ])
        self._fill(self._plugin_tree, rows)

    # 预留：配置变更时由主导航调用
    def update_config(self, config: CrawlConfig, project_root: Path | None = None) -> None:
        self._config = config
        if project_root is not None:
            self._project_root = project_root
        self._snap = _config_snapshot(config)
        self.refresh()

    # ------------------------------------------------------------------ #
    #  运行时间线（D-lite：读 workspace/state.sqlite3 的持久化事件）
    # ------------------------------------------------------------------ #
    def _state_db(self) -> Path | None:
        """定位当前项目的 state.sqlite3（不存在返回 None）。"""
        try:
            workspace = Path(self._config.workspace).expanduser()
            if not workspace.is_absolute():
                workspace = self._project_root / workspace
        except Exception:  # noqa: BLE001 - 只读检视，配置异常不崩
            return None
        db = workspace / "state.sqlite3"
        return db if db.is_file() else None

    def _build_timeline_options(self) -> None:
        """刷新运行下拉（最近运行，最多 50 条）。"""
        runs: list[dict[str, Any]] = []
        db = self._state_db()
        if db is not None:
            try:
                with StateStore(db) as state:
                    runs = state.list_runs(50)
            except Exception:  # noqa: BLE001 - 只读检视，DB 异常不崩界面
                runs = []
        self._timeline_combo.blockSignals(True)
        self._timeline_combo.clear()
        if not runs:
            self._timeline_combo.addItem(_("（无运行记录）"), "")
        for run in runs:
            label = "{}  {}  [{}]".format(
                str(run.get("started_at", ""))[:19],
                run.get("project_name", "?"),
                run.get("status", "?"),
            )
            self._timeline_combo.addItem(label, str(run["run_id"]))
        self._timeline_combo.blockSignals(False)
        self._render_timeline()

    def _render_timeline(self) -> None:
        """渲染选中运行的 状态迁移 + 阶段 checkpoint 时间线。"""
        run_id = str(self._timeline_combo.currentData() or "")
        self._timeline_tree.clear()
        self._timeline_meta.setText("")
        if not run_id:
            self._timeline_meta.setText(_("选择一次运行查看其事件时间线。"))
            return
        db = self._state_db()
        if db is None:
            self._timeline_meta.setText(_("当前项目还没有运行数据库（state.sqlite3）。"))
            return
        try:
            with StateStore(db) as state:
                run = next((r for r in state.list_runs(500) if r["run_id"] == run_id), None)
                events = state.run_events(run_id)
                stages = state.run_stages(run_id)
        except Exception as exc:  # noqa: BLE001 - 只读检视
            self._timeline_meta.setText(_("读取运行时间线失败：{0}").format(exc))
            return

        if run is not None:
            self._timeline_meta.setText(
                _("运行 {0} · {1} · 状态 {2} · 开始 {3}").format(
                    str(run_id)[:8],
                    run.get("project_name", "?"),
                    run.get("status", "?"),
                    str(run.get("started_at", "?"))[:19],
                )
            )

        rows: list[tuple[str, str, str]] = []
        for ev in events:
            rows.append((
                str(ev.get("created_at", ""))[:19],
                _("状态: {0} → {1}").format(ev.get("from_state", "?"), ev.get("to_state", "?")),
                str(ev.get("reason", "") or ""),
            ))
        for st in stages:
            rows.append((
                str(st.get("updated_at", ""))[:19],
                _("阶段: {0}").format(st.get("stage", "?")),
                "{} {}".format(st.get("status", "?"), st.get("idempotency_key", "") or ""),
            ))
        rows.sort(key=lambda item: item[0])
        for time_str, event, detail in rows:
            self._timeline_tree.addTopLevelItem(QTreeWidgetItem([time_str, event, detail]))
