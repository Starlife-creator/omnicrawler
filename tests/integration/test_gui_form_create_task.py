"""经 GUI **表单**创建任务：从空白表单填起，保存出的配置与填写内容对得上。

## 与既有两个 GUI 用例的分工

| 用例 | 覆盖的路径 |
|---|---|
| `test_gui_entry_run.py` | **注入**写好的 YAML → 点「运行」→ 子进程 → 结果页 |
| `test_gui_worker_local_task.py` | 运行器 + worker 子进程（4 种任务形态、产物正确性）|
| **本文件** | **表单 → 配置 → YAML** 这一段（新建 / 填表 / 填列表项选择器 / 保存草稿），并**跑到产物** |

## 为什么"表单创建"要单独验

账本里若干行的「下一项验收」是「经 GUI **表单创建**任务」。此前所有 GUI 证据都是
**把写好的 YAML 交给窗口**（`load_config`），并没有验过"用户从空白表单填出来"这条路 ——
而它恰恰是新手的第一条路径（§4.5 主流程：创建任务 → 小样预览 → 正式运行 → …）。

## 本轮顺带补上的能力（2026-09-14）

上一轮实测发现：**GUI 没有任何路径可以 author `extract.item_selector`**，于是"从零在表单里
建不出列表任务"（整页会被当成一条记录）。本轮给表单加了「列表项选择器」输入口，
`CrawlConfig` 也配了一对 `item_selector()` / `set_item_selector()`（与 `extract_mode()` 同源），
因此下面 `test_form_created_list_task_runs_end_to_end` 才成立：**从空白表单建列表任务 → 保存 → 运行 → 产物等于真值**。
"""

from __future__ import annotations

import contextlib
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

EXPECTED_FIELDS_SELECTORS = {"h1", "a", "time", ".author", ".description"}

#: 演示站点的真值（与 `_LIST_HTML` 一致）
EXPECTED = (("苹果", "11"), ("香蕉", "22"), ("樱桃", "33"))

_LIST_HTML = """<html><body><div class="list">
<div class="item"><h2 class="t">苹果</h2><span class="p">11</span></div>
<div class="item"><h2 class="t">香蕉</h2><span class="p">22</span></div>
<div class="item"><h2 class="t">樱桃</h2><span class="p">33</span></div>
</div></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 —— http.server 回调命名约定
        body = _LIST_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # 静音访问日志
        return


@contextlib.contextmanager
def _serve():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/list"
    finally:
        server.shutdown()
        server.server_close()


def _pump(app, predicate, *, timeout: float = 60.0, what: str = "") -> None:
    """条件等待（不依赖固定睡眠）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not predicate():
        app.processEvents()
        time.sleep(0.05)
    assert predicate(), f"等待超时：{what}"


@pytest.fixture(autouse=True)
def _no_blocking_dialogs(monkeypatch):
    """把**模态**对话框换成记录器 —— 离屏环境下模态框会永久阻塞，把"失败"变成"挂起"。

    这条来自实测教训：本文件里一个 YAML 写坏（`extract://n`）后，委托走
    `QMessageBox.critical("加载失败")`，用例**挂死 25 秒以上且不给任何线索**
    （靠 `faulthandler` 才定位到那一行）。改为记录后，失败会立刻变成可读的断言错误；
    用例还可以断言"不该弹窗"。
    """
    from PySide6.QtWidgets import QMessageBox

    raised: list[tuple[str, str]] = []

    def _recorder(*args, **_kwargs):
        title = str(args[1]) if len(args) > 1 else ""
        text = str(args[2]) if len(args) > 2 else ""
        raised.append((title, text))
        # 统一回 Yes：对 `question` 语义安全，对 critical/warning 的返回值无人使用。
        return QMessageBox.StandardButton.Yes

    for name in ("critical", "warning", "question", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(_recorder))
    return raised


def test_blocking_dialog_guard_is_wired(_no_blocking_dialogs) -> None:
    """守卫自身有效：调用 `QMessageBox.critical` 既**不阻塞**也被记录。

    没有这条时，守卫"看起来存在但其实没生效"不会被发现 —— 而它的失效形式是
    **用例挂死**（比失败更难查）。上一条实测教训：YAML 写坏 → 委托弹"加载失败" →
    整个用例卡在模态框上，靠 `faulthandler` 才定位到。
    """
    from PySide6.QtWidgets import QMessageBox

    QMessageBox.critical(None, "标题", "内容")
    assert _no_blocking_dialogs == [("标题", "内容")], _no_blocking_dialogs


def _window(tmp_path: Path, monkeypatch):
    """建一个指向临时工程根的 MainWindow（首启向导不弹）。"""
    from PySide6.QtWidgets import QApplication

    from omnicrawler.gui.main import MainWindow

    app = QApplication.instance() or QApplication([])
    # 必须用 monkeypatch：直接给类属性赋值会**泄漏**给同进程的后续用例。
    monkeypatch.setattr(MainWindow, "_on_first_launch", lambda self: None)

    window = MainWindow()
    window._project_root = tmp_path
    window._rebuild_project_components()
    return app, window


def _silence_side_effects(window) -> None:
    """关掉"打开目录 / 自动导出 / 提示音"等与断言无关的副作用。"""
    with contextlib.suppress(Exception):
        window._settings.auto_open_result = False
        window._settings.markdown_export_enabled = False
        window._settings.sound_enabled = False


def test_form_created_task_round_trips_into_yaml(tmp_path: Path, monkeypatch) -> None:
    """空白表单 → 填网址/描述 → 「开始」→ 「启发式补全字段」→ 「保存草稿」→ YAML 对得上。

    全程走**真实控件与真实信号链**：`_url_edit.setText` / `_desc_edit.setText` /
    `_start_btn.click()` / `_complete_btn.click()` / `_save_btn.click()`，不手工造配置对象。
    """
    from PySide6.QtWidgets import QFileDialog

    from omnicrawler.gui.core.config_serializer import load_yaml

    saved_path = tmp_path / "form_task.yaml"
    # 「保存草稿」在还没有路径时走"另存为"对话框 —— 换成直接返回临时路径（不弹模态）
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(saved_path), ""))
    )

    with _serve() as seed:
        app, window = _window(tmp_path, monkeypatch)
        _silence_side_effects(window)
        try:
            window._config_delegate.new_config()      # 真实「新建配置」入口
            assert window._config_path is None, "新建后不应有配置路径"
            assert not window._config.seed_urls, "新建的配置应为空白"

            canvas = window._task_canvas
            canvas._url_edit.setText(seed)                    # 表单：网址
            canvas._desc_edit.setText("采集整个栏目")          # 表单：一句话描述
            canvas._start_btn.click()                         # 表单：「开始」
            _pump(
                app,
                lambda: window._config.source_kind == "crawl",
                what="「开始」把草稿落到配置",
            )
            assert window._config.max_pages == 30, (
                f"栏目任务的页数预算应由草稿决定：{window._config.max_pages}"
            )

            canvas._complete_btn.click()                      # 表单：「启发式补全字段」
            _pump(app, lambda: len(window._config.fields) > 0, what="字段补全")
            field_names = [f.name for f in window._config.fields]

            canvas._save_btn.click()                          # 表单：「保存草稿」
            _pump(app, lambda: saved_path.is_file(), what="配置落盘")

            # —— 写出的 YAML 必须与表单里填的一致 ——
            saved = load_yaml(saved_path)
            assert saved.seed_urls == [seed], f"网址应来自表单：{saved.seed_urls}"
            assert saved.task_description == "采集整个栏目"
            assert saved.source_kind == "crawl", "栏目任务的来源类型应来自「开始」的草稿"
            assert saved.max_pages == 30
            assert [f.name for f in saved.fields] == field_names, "字段应随表单一起落盘"
            assert {f.selector for f in saved.fields} <= EXPECTED_FIELDS_SELECTORS, (
                f"补全字段用的是通用规则：{[f.selector for f in saved.fields]}"
            )
        finally:
            window.close()
            app.processEvents()


def test_form_edit_preserves_list_container(
    tmp_path: Path, monkeypatch, _no_blocking_dialogs
) -> None:
    """**透传保留**：打开一份带 `item_selector` 的列表配置，在表单里改一改再保存，容器不能被清空。

    这条连起"表单编辑"与既有 `test_gui_entry_run.py`（注入 YAML 后运行）：如果表单保存时把
    `item_selector` 写没了，那个用例能跑通只是因为**没经过保存**——这里补上这一环。
    """
    from omnicrawler.gui.core.config_serializer import load_yaml

    cfg_path = tmp_path / "list_task.yaml"
    cfg_path.write_text(
        "project: {name: form-edit, workspace: ws}\n"
        "source: {kind: static_html, seeds: [https://example.org/list]}\n"
        "extract:\n"
        "  mode: html\n"
        "  item_selector: div.item\n"
        "  fields:\n"
        "    标题: {selector: h2.t}\n"
        "outputs: {jsonl: true}\n",
        encoding="utf-8",
    )

    app, window = _window(tmp_path, monkeypatch)
    _silence_side_effects(window)
    try:
        window._config_delegate._open_recent(str(cfg_path))   # 真实「打开配置」入口
        assert window._config_path == cfg_path
        assert window._task_canvas._url_edit.text() == "https://example.org/list", (
            "表单应显示被打开配置的网址"
        )

        window._task_canvas._desc_edit.setText("改一下描述再保存")   # 表单编辑
        window._task_canvas._save_btn.click()                     # 表单保存（路径已知，不弹对话框）
        _pump(app, lambda: "改一下描述再保存" in cfg_path.read_text(encoding="utf-8"), what="保存完成")

        raw = cfg_path.read_text(encoding="utf-8")
        assert "item_selector: div.item" in raw, f"列表项选择器被保存流程弄丢了：\n{raw}"
        assert load_yaml(cfg_path).seed_urls == ["https://example.org/list"]
        assert _no_blocking_dialogs == [], f"打开/保存配置不该弹窗：{_no_blocking_dialogs}"
    finally:
        window.close()
        app.processEvents()


def test_form_edit_preserves_extract_mode_and_item_path(
    tmp_path: Path, monkeypatch, _no_blocking_dialogs
) -> None:
    """**透传保留（`mode` / `item_path`）**：JSON API 任务经表单保存后不能退回 `html`。

    `_sync_form_to_config` 只写 A 类字段（fields）；`extract.mode` 与 `json.item_path`
    属 B 类透传。旧实现把它们写死（`mode: html`），而 `_deep_overlay` 让 root 胜出 ⇒
    **打开一个可用的 JSON 配置再运行，会被静默降级成 HTML 抽取**。这里把该契约钉在
    「表单编辑 → 保存」这条路径上（此前只验过加载）。
    """
    from omnicrawler.gui.core.config_serializer import load_yaml

    cfg_path = tmp_path / "api_task.yaml"
    cfg_path.write_text(
        "project: {name: api-task, workspace: ws}\n"
        "source: {kind: rest, seeds: [https://api.example/users]}\n"
        "extract:\n"
        "  mode: json\n"
        "  item_path: $.data[*]\n"
        "  fields:\n"
        "    名称: {path: name}\n"
        "outputs: {jsonl: true}\n",
        encoding="utf-8",
    )

    app, window = _window(tmp_path, monkeypatch)
    _silence_side_effects(window)
    try:
        window._config_delegate._open_recent(str(cfg_path))
        window._task_canvas._desc_edit.setText("改描述后再保存")
        window._task_canvas._save_btn.click()
        _pump(app, lambda: "改描述后再保存" in cfg_path.read_text(encoding="utf-8"), what="保存完成")

        raw = cfg_path.read_text(encoding="utf-8")
        assert "mode: json" in raw, f"JSON 抽取模式被表单保存改掉了：\n{raw}"
        assert "item_path: $.data[*]" in raw, f"item_path 被表单保存弄丢了：\n{raw}"
        assert load_yaml(cfg_path).extract_mode() == "json"
        assert _no_blocking_dialogs == [], f"打开/保存配置不该弹窗：{_no_blocking_dialogs}"
    finally:
        window.close()
        app.processEvents()


def test_form_can_author_list_container_and_trial_gate_rearms(
    tmp_path: Path, monkeypatch, _no_blocking_dialogs
) -> None:
    """表单能**写**列表项选择器，且改它会**让试跑失效**（不能拿旧试跑结果放行）。

    上一轮这里钉的是"GUI 无法 author `item_selector`"这条限制；本轮补上输入口后，
    该限制不再成立，于是改成正面契约 + 一条安全约束：
    **容器决定"一条记录"的边界，改了它必须重新试跑**。
    """
    app, window = _window(tmp_path, monkeypatch)
    _silence_side_effects(window)
    try:
        window._config_delegate.new_config()
        canvas = window._task_canvas
        canvas._url_edit.setText("https://example.org/list")
        canvas._item_selector_edit.setText("div.item")          # 表单：列表项选择器
        assert window._config.item_selector() == "div.item", "表单值应同步进配置"

        # 闸门：试跑通过后运行按钮可用；改容器 ⇒ 立刻失效（与字段表同域）
        canvas.set_trial_result(True, "试跑通过", {})
        assert canvas._run_btn.isEnabled(), "试跑一致后运行按钮应可用"
        canvas._item_selector_edit.setText("li.product")
        assert not canvas._run_btn.isEnabled(), (
            "改了列表项选择器却仍可运行 —— 等于让用户拿旧试跑结果去跑全量"
        )
        assert window._config.item_selector() == "li.product"
        assert _no_blocking_dialogs == [], _no_blocking_dialogs
    finally:
        window.close()
        app.processEvents()


def test_form_created_list_task_runs_end_to_end(tmp_path: Path, monkeypatch) -> None:
    """**从空白表单建一个列表任务 → 保存 → 运行 → 产物等于真值。**

    这是账本里「经 GUI 表单**创建**任务」这条验收的正面证据：全程不注入任何写好的配置 ——
    网址、列表项选择器、字段规则都由**表单控件**产生，保存成 YAML 后交给真实 worker
    子进程运行，最后核对 `records.csv` 的三条真值。

    （试跑本身由 `test_first_task_journey.py` 覆盖；这里用官方 API
    `set_trial_result` 满足"试跑一致"闸门，不伪造按钮状态。）
    """
    import csv as _csv

    from PySide6.QtWidgets import QFileDialog

    from omnicrawler.gui.core.config_model import FieldDef

    saved_path = tmp_path / "form_list_task.yaml"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(saved_path), ""))
    )

    with _serve() as seed:
        app, window = _window(tmp_path, monkeypatch)
        _silence_side_effects(window)
        window._omnicrawler_available = True
        try:
            window._config_delegate.new_config()
            canvas = window._task_canvas
            canvas._url_edit.setText(seed)                    # 表单：网址
            canvas._desc_edit.setText("单页")                 # 表单：一句话描述
            canvas._start_btn.click()                         # 表单：「开始」
            _pump(app, lambda: window._config.seed_urls == [seed], what="草稿落到配置")

            canvas._item_selector_edit.setText("div.item")    # 表单：列表项选择器
            for name, selector in (("标题", "h2.t"), ("价格", "span.p")):
                # 与「＋ 添加字段」按钮同一条路径（模型 append → 标脏）
                canvas._fields_model.append(
                    FieldDef(name=name, selector=selector, selector_type="css")
                )
                canvas._on_field_changed()
            # 本机站点需**显式放行**：`http.allow_private_network` 是安全默认，
            # 产品**故意不**为它提供表单控件（默认禁止访问本机 / 内网 / 保留地址）。
            # 这里与 `test_first_task_journey.py` 同款处理；除此之外，任务形状
            # （网址 / 列表项选择器 / 字段）全部由表单控件产生。
            window._config.passthrough.setdefault("http", {})["allow_private_network"] = True
            canvas._save_btn.click()                          # 表单：「保存草稿」
            _pump(app, lambda: saved_path.is_file(), what="配置落盘")
            assert window._config_path == saved_path
            canvas.set_trial_result(True, "试跑通过：3 条记录", {})   # 满足试跑闸门
            assert canvas._run_btn.isEnabled()

            window._run_btn.click()                           # 真实「运行」
            _pump(
                app,
                lambda: window._task_runner.state in {"finished", "error"},
                timeout=180,
                what="任务到达终态",
            )
            assert window._task_runner.state == "finished", (
                f"表单创建的任务应成功结束：{window._task_runner.state}"
            )
            assert window._status_text.text() == "已完成"

            # workspace 按**项目根**解析（`run_controller` 里 `_project_root / config.workspace`）
            workspace = tmp_path / window._config.workspace
            records_csv = workspace / "output" / "records.csv"
            assert records_csv.is_file(), (
                f"应产出 records.csv：{sorted(p.name for p in (workspace / 'output').glob('*'))}"
            )
            rows = list(_csv.DictReader(records_csv.open(encoding="utf-8-sig")))
            assert {(r["标题"], r["价格"]) for r in rows} == set(EXPECTED), rows
            assert len(rows) == len(EXPECTED), f"应恰好交付 {len(EXPECTED)} 条：{len(rows)}"
        finally:
            with contextlib.suppress(Exception):
                window._task_runner._backend.shutdown()
            with contextlib.suppress(Exception):
                window._task_runner._poller.stop()
            window.close()
            app.processEvents()


@pytest.mark.parametrize("intent", ["单页", "采集整个栏目"])
def test_draft_intent_controls_scope_budget(tmp_path: Path, monkeypatch, intent: str) -> None:
    """「开始」的意图决定来源类型与页数预算（单页 1 页 / 栏目 30 页）。

    这条锁住 §4.5「自然语言输入生成可审阅、可修改的任务设置」里最容易被悄悄改坏的映射。
    """
    app, window = _window(tmp_path, monkeypatch)
    _silence_side_effects(window)
    try:
        window._config_delegate.new_config()
        canvas = window._task_canvas
        canvas._url_edit.setText("https://example.org/list")
        canvas._desc_edit.setText(intent)
        canvas._start_btn.click()
        expected_kind = "crawl" if intent == "采集整个栏目" else "static_html"
        _pump(app, lambda: window._config.source_kind == expected_kind, what=f"{intent} 的草稿")
        expected_pages = 30 if expected_kind == "crawl" else 1
        assert window._config.max_pages == expected_pages
    finally:
        window.close()
        app.processEvents()


# ── 2026-09-14：「分析页面并填字段」（把产品自带分析器接进表单） ──────────────

_SIDEBAR_HTML = (
    '<html><body><aside class="sidebar">'
    + "".join(f'<a href="/{c}">{c}</a>' for c in "abcd")
    + "</aside></body></html>"
)


@contextlib.contextmanager
def _serve_html(html: str):
    """起一个只返回给定 HTML 的本地站点。"""
    handler = type(
        "_H", (BaseHTTPRequestHandler,), {
            "do_GET": lambda self: (
                self.send_response(200),
                self.send_header("Content-Type", "text/html; charset=utf-8"),
                self.send_header("Content-Length", str(len(html.encode("utf-8")))),
                self.end_headers(),
                self.wfile.write(html.encode("utf-8")),
            ),
            "log_message": lambda self, *_a: None,
        },
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/list"
    finally:
        server.shutdown()
        server.server_close()


def _record_toasts(monkeypatch) -> dict[str, list[str]]:
    """把 Toast 换成记录器，便于断言"到底告诉了用户什么"。"""
    from omnicrawler.gui.widgets.toast import ToastManager

    seen: dict[str, list[str]] = {"success": [], "info": [], "warning": [], "error": []}

    def _make(kind: str):
        def _recorder(self, message, **_kwargs):
            seen[kind].append(str(message))
            return None

        return _recorder

    for kind in seen:
        monkeypatch.setattr(ToastManager, kind, _make(kind))
    return seen


def _allow_private(window) -> None:
    """本机站点需显式放行（`allow_private_network` 是安全默认，无表单控件）。"""
    window._config.passthrough.setdefault("http", {})["allow_private_network"] = True


def test_analyze_page_fills_container_and_real_selectors(tmp_path: Path, monkeypatch) -> None:
    """点「分析页面并填字段」→ 填入**真实**容器与选择器（不是通用规则）。

    对照：同一场景点「启发式补全字段」只会得到 `h1`/`a`/`time`/`.author`/`.description`
    这类与目标页无关的通用规则；本按钮走的是产品自带 DOM 分析器（CLI `analyze` 同源）。
    """
    seen = _record_toasts(monkeypatch)
    with _serve() as seed:
        app, window = _window(tmp_path, monkeypatch)
        _silence_side_effects(window)
        try:
            window._config_delegate.new_config()
            canvas = window._task_canvas
            assert not canvas._analyze_btn.isEnabled(), "没有网址时分析按钮应禁用"
            canvas._url_edit.setText(seed)
            assert canvas._analyze_btn.isEnabled(), "填了网址后分析按钮应可用"

            _allow_private(window)
            canvas._analyze_btn.click()
            _pump(app, lambda: bool(window._config.fields), timeout=120, what="分析完成")

            assert canvas._item_selector_edit.text().endswith("div.item"), (
                f"应填入真实容器：{canvas._item_selector_edit.text()!r}"
            )
            selectors = {f.selector for f in window._config.fields}
            assert {"h2.t", "span.p"} <= selectors, f"应填入页面真实选择器：{selectors}"
            # 按钮复位（同一按钮可再次使用）
            assert canvas._analyze_btn.text() == "🔍 分析页面并填字段"
            assert canvas._analyze_btn.isEnabled()
            assert seen["success"], "应给出成功提示"
        finally:
            window.close()
            app.processEvents()


def test_analyze_page_refuses_navigation_only_container(tmp_path: Path, monkeypatch) -> None:
    """只在页面框架（侧边栏 / 导航 / 页脚）里找到重复元素 ⇒ **不填容器**并明确警告。

    复用分析器/自动配置门禁的同一判据（`_is_chrome_path`）：把侧边栏当列表交给用户，
    比"什么都不填"更糟。
    """
    seen = _record_toasts(monkeypatch)
    with _serve_html(_SIDEBAR_HTML) as seed:
        app, window = _window(tmp_path, monkeypatch)
        _silence_side_effects(window)
        try:
            window._config_delegate.new_config()
            canvas = window._task_canvas
            canvas._url_edit.setText(seed)
            _allow_private(window)
            canvas._analyze_btn.click()
            _pump(
                app,
                lambda: canvas._analyze_btn.text() == "🔍 分析页面并填字段",
                timeout=120,
                what="分析结束",
            )
            app.processEvents()

            assert canvas._item_selector_edit.text() == "", "页面框架容器不应被填入"
            assert seen["warning"], "应明确警告：只在页面框架里找到重复元素"
        finally:
            window.close()
            app.processEvents()


def test_analyze_page_without_url_only_informs(tmp_path: Path, monkeypatch) -> None:
    """没有网址时不发起抓取，只提示 —— 不假装分析过。"""
    seen = _record_toasts(monkeypatch)
    app, window = _window(tmp_path, monkeypatch)
    _silence_side_effects(window)
    try:
        window._config_delegate.new_config()
        canvas = window._task_canvas
        canvas._url_edit.setText("")
        canvas._analyze_btn.click()
        app.processEvents()
        assert seen["info"], "应提示先填入口网址"
        assert not seen["success"], "不该假装成功"
        assert window._config.fields == []
    finally:
        window.close()
        app.processEvents()
