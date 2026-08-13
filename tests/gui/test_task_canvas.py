"""P0 任务画布验收测试：无 AI 全流程、试跑失效、外部编辑锁定、运行唯一出口。"""
from __future__ import annotations

import importlib.util

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PyQt6") is None,
    reason="GUI smoke test requires PyQt6",
)

_OFFSCREEN = "QT_QPA_PLATFORM"

# QApplication 必须保持模块级 Python 引用：若只在 helper 函数局部创建，
# 函数返回后 wrapper 被 GC 会连带销毁整个 Qt 控件树（含画布子控件）。
_APP = None


def _ensure_app() -> None:
    global _APP
    if _APP is None:
        from PyQt6.QtWidgets import QApplication

        _APP = QApplication.instance() or QApplication([])


def _make_canvas(monkeypatch):
    monkeypatch.setenv(_OFFSCREEN, "offscreen")
    _ensure_app()

    from omnicrawl.gui.core.config_model import CrawlConfig
    from omnicrawl.gui.views.task_canvas import TaskCanvas

    # 不要 show()/processEvents()——offscreen 下 import gui.main 后
    # processEvents 会触发 PyQt 延迟删除使画布整树失效；本测试只断言状态属性。
    canvas = TaskCanvas(CrawlConfig())
    return canvas


def _set_first_field(canvas, name="标题", selector="", kind="css"):
    """模拟用户添加并编辑首行字段（触发 model.dataChanged → 标 field 脏）。"""
    from omnicrawl.gui.core.config_model import FieldDef

    canvas._fields_model.append(FieldDef(name=name, selector=selector, selector_type=kind))
    canvas._fields_model.setData(canvas._fields_model.index(0, 0), name)


def _patch_editor_dialog(monkeypatch, choose_load: bool):
    """将外部编辑冲突对话框替换为无阻塞，并返回「被点按钮」引用。"""
    from PyQt6.QtWidgets import QMessageBox

    captured: dict = {}
    real_add = QMessageBox.addButton  # 必须在 patch 前捕获原始方法

    def fake_add(self, text: str, role):
        btn = real_add(self, text, role)
        if "加载" in text:
            captured["load"] = btn
        else:
            captured["keep"] = btn
        return btn

    def fake_clicked(self):
        return captured["load"] if choose_load else captured["keep"]

    monkeypatch.setattr(QMessageBox, "addButton", fake_add)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QMessageBox, "clickedButton", fake_clicked)
    return captured


def test_manual_flow_without_ai(monkeypatch):
    """黄金标尺：拿走提示后，无 AI 也能走通 粘贴网址→生成→试跑→全量运行。"""
    canvas = _make_canvas(monkeypatch)

    # ① 粘贴网址即可点「开始」（无需 AI）
    assert not canvas._start_btn.isEnabled()
    canvas._url_edit.setText("https://example.org/news")
    assert canvas._start_btn.isEnabled()

    # ② 生成草稿：草稿区展开 + 摘要可见
    canvas._on_start()
    assert not canvas._draft_section.collapsed()
    assert "example.org/news" in canvas._summary_label.text()

    # ③ 试跑按钮可用；试跑通过后「保存并全量运行」出现并启用
    assert canvas._trial_btn.isEnabled()
    assert not canvas._run_btn.isEnabled()
    canvas.set_trial_result(True, "状态：ok\n处理页面：3")
    assert canvas._run_btn.isEnabled()

    # ④ 运行唯一出口：交付区无任何运行按钮
    run_buttons = [
        b.text() for b in canvas._delivery_section.findChildren(type(canvas._run_btn))
        if "运行" in b.text()
    ]
    assert run_buttons == []
    canvas.deleteLater()


def test_trial_stale_invalidation(monkeypatch):
    """字段/草稿变更 → 试跑状态自动失效：stale 警告 + 运行按钮禁用。"""
    canvas = _make_canvas(monkeypatch)
    canvas._url_edit.setText("https://example.org/news")
    canvas._on_start()
    canvas.set_trial_result(True, "状态：ok\n处理页面：3")
    assert canvas._run_btn.isEnabled()
    assert not canvas._stale_warning.isVisibleTo(canvas)

    # 字段变更（模拟用户编辑字段表格）→ 试跑失效
    _set_first_field(canvas)
    assert canvas._stale_warning.isVisibleTo(canvas)
    assert not canvas._run_btn.isEnabled()
    assert not canvas._trial_ok

    # 重新试跑通过 → 恢复可用
    canvas.set_trial_result(True, "状态：ok\n处理页面：3")
    assert canvas._run_btn.isEnabled()
    canvas.deleteLater()


def test_external_edit_conflict_locks_and_blocks_save(monkeypatch):
    """YAML 外部编辑冲突：锁定态禁保存/禁编辑，用户二选一后解锁。"""
    from PyQt6.QtWidgets import QMessageBox

    canvas = _make_canvas(monkeypatch)
    canvas._url_edit.setText("https://example.org/news")
    canvas._on_start()
    # 制造未提交修改
    canvas._max_pages.setValue(5)
    assert canvas._dirty

    from omnicrawl.gui.core.config_model import CrawlConfig

    updated = CrawlConfig()
    updated.seed_urls = ["https://external.example.org"]
    updated.max_pages = 99

    _patch_editor_dialog(monkeypatch, choose_load=True)
    seen_locked: list[bool] = []

    def fake_exec(box):
        # 对话框展示期间画布应处于锁定态：禁保存
        seen_locked.append(canvas.is_locked())
        assert not canvas._save_btn.isEnabled()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    canvas.notify_external_edit(updated)

    assert seen_locked == [True]
    # 选择「加载 YAML 覆盖草稿」：配置被替换、脏标记清除、解锁恢复
    assert not canvas.is_locked()
    assert not canvas._dirty
    assert canvas._config.seed_urls == ["https://external.example.org"]
    assert canvas._config.max_pages == 99
    assert canvas._save_btn.isEnabled()
    canvas.deleteLater()


def test_external_edit_keep_canvas_preserves_draft(monkeypatch):
    """冲突时选择「放弃 YAML，保留画布」：本地编辑态与脏标记被保留。"""
    canvas = _make_canvas(monkeypatch)
    canvas._url_edit.setText("https://example.org/news")
    canvas._on_start()
    canvas._max_pages.setValue(7)
    assert canvas._dirty

    from omnicrawl.gui.core.config_model import CrawlConfig

    updated = CrawlConfig()
    updated.seed_urls = ["https://external.example.org"]

    _patch_editor_dialog(monkeypatch, choose_load=False)
    canvas.notify_external_edit(updated)

    assert not canvas.is_locked()
    assert canvas._dirty  # 保留画布编辑态，继续阻断旧回写
    assert canvas._config.seed_urls == ["https://example.org/news"]
    assert canvas._config.max_pages == 7
    canvas.deleteLater()


def test_lock_blocks_editing_and_save(monkeypatch):
    """set_locked 下所有编辑控件与保存按钮禁用。"""
    canvas = _make_canvas(monkeypatch)
    canvas.set_locked(True)

    assert not canvas._save_btn.isEnabled()
    assert not canvas._url_edit.isEnabled()
    assert not canvas._desc_edit.isEnabled()
    assert not canvas._fields_table.isEnabled()
    assert not canvas._trial_btn.isEnabled()
    assert not canvas._start_btn.isEnabled()

    # 锁定期间外部改动不得污染本地状态
    canvas._mark_dirty()
    assert not canvas._dirty

    canvas.set_locked(False)
    assert canvas._save_btn.isEnabled()
    canvas.deleteLater()


# ────────────────────────── P1 验收 ──────────────────────────


def test_domain_dirty_flags_are_independent(monkeypatch):
    """按域脏标记（PRD §2.2.1）：改输出只标 output，改字段只标 field，互不阻塞。"""
    canvas = _make_canvas(monkeypatch)
    canvas._url_edit.setText("https://example.org/news")
    canvas._on_start()
    canvas._clear_dirty()
    assert canvas._dirty_domains == set()

    canvas._format_checks[0].setChecked(False)
    assert canvas._dirty_domains == {"output"}

    _set_first_field(canvas)
    assert canvas._dirty_domains == {"output", "field"}

    canvas._monitor_chk.setChecked(True)
    assert canvas._dirty_domains == {"output", "field", "schedule"}

    canvas._max_pages.setValue(5)
    assert "scope" in canvas._dirty_domains
    canvas.deleteLater()


def test_field_hash_binds_trial_to_fields(monkeypatch):
    """试跑状态与字段指纹绑定（PRD §2.2.3）：字段变更即 stale，重试跑恢复。"""
    canvas = _make_canvas(monkeypatch)
    canvas._url_edit.setText("https://example.org/news")
    canvas._on_start()
    _set_first_field(canvas)

    canvas.set_trial_result(True, "状态：ok\n处理页面：3")
    assert canvas.trial_matches_fields()
    assert canvas._run_btn.isEnabled()

    # 字段变更 → 指纹失配 → stale + 运行禁用
    canvas._fields_model.setData(canvas._fields_model.index(0, 1), "h1.title")
    assert not canvas.trial_matches_fields()
    assert not canvas._run_btn.isEnabled()

    # 重新试跑通过 → 恢复
    canvas.set_trial_result(True, "状态：ok\n处理页面：3")
    assert canvas.trial_matches_fields()
    assert canvas._run_btn.isEnabled()
    canvas.deleteLater()


def test_summary_card_sections_and_simple_mode(monkeypatch):
    """草稿卡片分节渲染（PRD §3.2）：专业全分节，简单模式仅核心 3 项。"""
    canvas = _make_canvas(monkeypatch)
    canvas._url_edit.setText("https://example.org/news")
    canvas._on_start()

    text = canvas._summary_label.text()
    assert "输出格式" in text
    assert "资源预算" in text
    assert "可修改" in text

    canvas.set_simple_mode(True)
    text = canvas._summary_label.text()
    assert "输出格式" not in text
    assert "变化监测" not in text
    assert "入口" in text and "采集方式" in text and "预计页数" in text
    canvas.deleteLater()


def test_template_recommendation_gate_in_draft(monkeypatch):
    """B-2 闸门前移（PRD §3.2）：本地 L1/L2 推荐 + 可忽略，绝不阻断。"""
    canvas = _make_canvas(monkeypatch)
    canvas._url_edit.setText("https://example.org/news")
    canvas._on_start()

    # 本地分类后：覆盖下拉可用 + 徽标显示推荐（来源或置信度）
    assert canvas._template_combo.isEnabled()
    assert canvas._ignore_rec_btn.isEnabled()
    assert "置信度" in canvas._source_badge.text() or "模板" in canvas._source_badge.text()

    # 忽略推荐 → 手动配置，零障碍
    canvas._ignore_recommendation()
    assert not canvas._template_combo.isEnabled()
    assert "手动" in canvas._source_badge.text()
    canvas.deleteLater()


def test_heuristic_complete_is_upsert(monkeypatch):
    """智能补全去重追加（PRD §3.3）：绝不覆盖用户字段，重复补全不重复。"""
    canvas = _make_canvas(monkeypatch)
    canvas._url_edit.setText("https://example.org/news")
    canvas._on_start()
    canvas._recommendation = None  # 强制走通用规则路径，保证确定性

    # 用户已有「标题」字段（用户版选择器）
    _set_first_field(canvas, name="标题", selector="h1.my-title")
    before = len(canvas._fields_model.rows())

    canvas._heuristic_complete_fields()
    after = len(canvas._fields_model.rows())
    # 通用规则 5 项中 1 个同名「标题」被跳过 → 追加 4
    assert after == before + 4

    # 用户版「标题」未被覆盖
    for field in canvas._fields_model.rows():
        if field.name == "标题":
            assert field.selector == "h1.my-title"
            break
    else:
        pytest.fail("标题字段丢失")

    # 再次补全不重复
    canvas._heuristic_complete_fields()
    assert len(canvas._fields_model.rows()) == after
    canvas.deleteLater()


def test_mode_switch_keeps_draft(monkeypatch):
    """AI/模式开关不重置当前草稿（PRD §3.1）：切换简单/专业后草稿与字段保留。"""
    canvas = _make_canvas(monkeypatch)
    canvas._url_edit.setText("https://example.org/news")
    canvas._on_start()
    _set_first_field(canvas)

    url_before = canvas._config.seed_urls[0]
    canvas.set_simple_mode(True)
    assert canvas._config.seed_urls == [url_before]
    assert len(canvas._fields_model.rows()) == 1

    canvas.set_simple_mode(False)
    assert canvas._config.seed_urls == [url_before]
    assert len(canvas._fields_model.rows()) == 1
    canvas.deleteLater()


# ────────────────────────── P2 验收 ──────────────────────────


def test_probe_debounce_fires_only_for_valid_url(monkeypatch):
    """URL 探活防抖（PRD §3.1）：有效 URL 调度 timer 并发出信号；无效/锁定不触发。"""
    seen: list[str] = []
    canvas = _make_canvas(monkeypatch)
    canvas.probe_requested.connect(seen.append)

    # 无效 URL：不调度探活
    canvas._url_edit.setText("not-a-url")
    assert not canvas._probe_timer.isActive()
    assert canvas._probe_badge.text() == ""

    # 有效 URL：timer 启动（600ms 防抖）
    canvas._url_edit.setText("https://example.org/news")
    assert canvas._probe_timer.isActive()
    canvas._probe_timer.stop()
    canvas._fire_probe()
    assert seen == ["https://example.org/news"]
    assert "正在探测" in canvas._probe_badge.text()

    # 锁定态：fire 不再发信号
    seen.clear()
    canvas.set_locked(True)
    canvas._fire_probe()
    assert seen == []
    canvas.deleteLater()


def test_probe_result_badge_and_stale_discard(monkeypatch):
    """探活结果回填徽标（PRD §3.1）；URL 已变更的过期结果被丢弃。"""
    canvas = _make_canvas(monkeypatch)
    canvas._url_edit.setText("https://example.org/news")

    report = {
        "page_type": "list",
        "dynamic": False,
        "pagination": ["numbered-or-offset"],
        "recommendations": [],
    }
    canvas.set_probe_result("https://example.org/news", report)
    text = canvas._probe_badge.text()
    assert "可访问" in text and "列表页" in text and "静态" in text and "分页线索" in text

    # URL 变更后旧结果被丢弃（徽标被输入逻辑清空）
    canvas._url_edit.setText("https://example.org/other")
    canvas._probe_timer.stop()
    canvas._set_probe_badge("")
    canvas.set_probe_result("https://example.org/news", report)
    assert canvas._probe_badge.text() == ""
    canvas.deleteLater()


def test_probe_failure_silently_degrades(monkeypatch):
    """探活失败静默降级（PRD §3.1）：徽标提示，不抛异常、不影响主流程。"""
    canvas = _make_canvas(monkeypatch)
    canvas._url_edit.setText("https://example.org/news")

    canvas.set_probe_failed("https://example.org/news", "TimeoutError: timeout")
    assert "手动配置" in canvas._probe_badge.text()
    assert canvas._start_btn.isEnabled()  # 主流程不受影响

    # 过期失败也不显示
    canvas._set_probe_badge("")
    canvas.set_probe_failed("https://example.org/other", "TimeoutError: timeout")
    assert canvas._probe_badge.text() == ""
    canvas.deleteLater()


def test_trial_statusbar_always_visible_when_collapsed(monkeypatch):
    """验证区永不消失（PRD §2.4）：折叠后 body 隐藏，但底部状态栏常驻显示摘要。"""
    canvas = _make_canvas(monkeypatch)
    canvas._url_edit.setText("https://example.org/news")
    canvas._on_start()
    canvas.set_trial_result(True, "状态：ok\n处理页面：3")
    assert "最近试跑" in canvas._status_text.text()

    # 折叠验证区：body 内容隐藏，状态栏与「查看详情」按钮仍可见
    canvas._collapse_section(canvas._trial_section, True)
    assert canvas._trial_section.collapsed()
    assert not canvas._trial_result_label.isVisibleTo(canvas)
    assert canvas._status_text.isVisibleTo(canvas)
    assert canvas._status_view_btn.isVisibleTo(canvas)

    # 状态栏「查看详情」→ 展开验证区，body 恢复可见
    canvas._expand_trial_section()
    assert not canvas._trial_section.collapsed()
    assert canvas._trial_result_label.isVisibleTo(canvas)

    # stale 状态也同步到状态栏（字段变更后折叠也能看到）
    _set_first_field(canvas)
    assert "重新试跑" in canvas._status_text.text()
    canvas.deleteLater()


def test_trial_history_keeps_last_three(monkeypatch):
    """试跑历史保留最近 3 次（PRD §3.4）：超限自动淘汰最旧记录。"""
    canvas = _make_canvas(monkeypatch)
    canvas._url_edit.setText("https://example.org/news")
    canvas._on_start()

    for index in range(1, 5):
        canvas.set_trial_result(index % 2 == 0, f"状态：第{index}次\n处理页面：3")

    assert len(canvas._trial_history) == 3
    text = canvas._history_box.text()
    assert "第2次" in text and "第3次" in text and "第4次" in text
    assert "第1次" not in text  # 最旧记录被淘汰
    assert canvas._history_btn.isVisibleTo(canvas)

    # 展开历史可对比
    canvas._toggle_trial_history()
    assert canvas._history_box.isVisibleTo(canvas)
    canvas.deleteLater()


def test_rejection_records_diagnostic_snapshot(monkeypatch, tmp_path):
    """拒绝理由采集（PRD §3.2）：👎 标签携带完整诊断快照落盘；无快照不入库。"""
    import json as _json

    from omnicrawl.core.categorizer import CategorizeResult

    canvas = _make_canvas(monkeypatch)
    canvas._url_edit.setText("https://example.org/news")
    canvas._on_start()
    canvas._project_root = str(tmp_path)
    canvas._recommendation = CategorizeResult(
        url="https://example.org/news",
        template_id="news/article",
        confidence=0.95,
        hit_source="L2",
        raw_requested_template="news/article",
        fallback_used=False,
        reason="L2: 映射命中 news 模板",
        eTLD1="example.org",
    )
    canvas._reject_btn.setEnabled(True)

    canvas._record_rejection("字段太少")

    path = tmp_path / "workspace" / "logs" / "template_feedback.jsonl"
    assert path.exists()
    snapshot = _json.loads(path.read_text(encoding="utf-8").strip())
    assert snapshot["domain"] == "example.org"
    assert snapshot["template_id"] == "news/article"
    assert snapshot["hit_source"] == "L2"
    assert snapshot["confidence"] == 0.95
    assert snapshot["reject_label"] == "字段太少"
    assert snapshot["action"] == "template_rejection"

    # 无快照（模板缺失）不入库
    canvas._recommendation = CategorizeResult(
        url="", template_id="", confidence=0.0, hit_source="",
        raw_requested_template="", fallback_used=False,
    )
    canvas._record_rejection("网址不匹配")
    content = path.read_text(encoding="utf-8").strip()
    assert len(content.splitlines()) == 1  # 未追加
    canvas.deleteLater()


def test_field_table_progressive_load(monkeypatch):
    """字段表格渐进披露（PRD §3.3）：50 字段默认只显示前 10 条，可展开全部。"""
    from omnicrawl.gui.core.config_model import FieldDef

    canvas = _make_canvas(monkeypatch)
    canvas._url_edit.setText("https://example.org/news")
    canvas._on_start()

    fields = [FieldDef(name=f"字段{i}", selector=f".s{i}", selector_type="css") for i in range(50)]
    canvas._fields_model.set_fields(fields)

    # 数据就绪后：模型仅暴露前 10 行（首屏只绘制可见行）
    assert canvas._fields_model.rowCount() == 10
    assert canvas._fields_model.hidden_count() == 40
    assert len(canvas._fields_model.rows()) == 50
    assert canvas._more_fields_btn.isVisibleTo(canvas)
    assert "40" in canvas._more_fields_btn.text()

    # 点击「加载更多」→ 全部展开，按钮消失
    canvas._show_all_fields()
    assert canvas._fields_model.rowCount() == 50
    assert canvas._fields_model.hidden_count() == 0
    assert not canvas._more_fields_btn.isVisibleTo(canvas)
    canvas.deleteLater()


def test_field_table_first_screen_render_baseline(monkeypatch):
    """计时口径（PRD §3.3）：50 字段数据就绪 → 首屏渲染 <500ms。"""
    import time

    from omnicrawl.gui.core.config_model import FieldDef

    canvas = _make_canvas(monkeypatch)
    canvas._url_edit.setText("https://example.org/news")
    canvas._on_start()

    fields = [FieldDef(name=f"字段{i}", selector=f".s{i}", selector_type="css") for i in range(50)]
    start = time.perf_counter()
    canvas._fields_model.set_fields(fields)
    canvas._fields_table.ensurePolished()  # 触发首屏布局/绘制准备
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 500, f"首屏渲染 {elapsed_ms:.0f}ms 超出 500ms 基线"
    canvas.deleteLater()


# ────────────────────────── P4 验收 ──────────────────────────


def _make_ai_draft(url: str, **overrides):
    """构造最小 NaturalLanguageDraft 形状（SimpleNamespace，模拟 compile_with_ai 产物）。"""
    from types import SimpleNamespace

    task = SimpleNamespace(
        url=url, intent="monitor_changes", source_kind="static_html",
        max_pages=10, download_files=False, process_pdf=False,
        monitor_changes=True, output_formats=("jsonl",),
    )
    base = SimpleNamespace(
        ai_enhanced=True, task=task, topics=("新闻",),
        ai_assumptions=(), ai_recommendations=(),
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


# ── P4-1 视觉点选提升为主流程 ──


def test_visual_pick_hidden_in_simple_mode(monkeypatch):
    """视觉点选为进阶入口：字段区展开后可见，简单模式隐藏，专业模式恢复。"""
    canvas = _make_canvas(monkeypatch)
    canvas._ai_plan_enabled = False
    canvas._url_edit.setText("https://example.org/news")
    canvas._on_start()  # 生成草稿展开字段区（初始折叠）
    assert canvas._visual_pick_btn.isVisibleTo(canvas)

    canvas.set_simple_mode(True)
    assert not canvas._visual_pick_btn.isVisibleTo(canvas)
    assert canvas._complete_btn.isVisibleTo(canvas)  # 启发式补全仍是主按钮

    canvas.set_simple_mode(False)
    assert canvas._visual_pick_btn.isVisibleTo(canvas)
    canvas.deleteLater()


def test_visual_candidates_upsert_and_selector_kind(monkeypatch):
    """视觉点选候选 Upsert 追加（PRD §3.3）：同名加后缀不覆盖；XPath 正确识别。"""
    from types import SimpleNamespace

    from omnicrawl.gui.core.config_model import FieldDef

    canvas = _make_canvas(monkeypatch)
    canvas._ai_plan_enabled = False  # 避免 _on_start 启动真实后台线程
    canvas._url_edit.setText("https://example.org/news")
    canvas._on_start()

    # 用户已有「标题」字段（手动版选择器），视觉点选不得覆盖
    canvas._fields_model.append(FieldDef(name="标题", selector="h1.user", selector_type="css"))

    candidates = [
        SimpleNamespace(suggested_name="标题", css="h1.visual", xpath="//h1", attribute="text"),
        SimpleNamespace(suggested_name="日期", css="", xpath="//time", attribute="text"),
        SimpleNamespace(suggested_name="链接", css="a.item", xpath="", attribute="href"),
    ]
    canvas._apply_visual_candidates(candidates)

    rows = canvas._fields_model.rows()
    # 用户版「标题」未被覆盖；视觉版以「标题_2」追加
    by_name = {f.name: f for f in rows}
    assert by_name["标题"].selector == "h1.user"
    assert by_name["标题_2"].selector == "h1.visual"
    assert by_name["标题_2"].attribute == "text"
    # 纯 XPath → xpath 类型 + fallback 为 None；CSS + XPath 兜底保留 fallback_xpath
    assert by_name["日期"].selector_type == "xpath"
    assert by_name["日期"].selector == "//time"
    assert by_name["链接"].selector_type == "css"
    assert by_name["链接"].attribute == "href"
    assert by_name["链接"].fallback_xpath is None
    canvas.deleteLater()


def test_visual_pick_without_url_degrades_gracefully(monkeypatch):
    """未填网址时视觉点选静默提示，不弹对话框、不崩溃（双轨：手动配置不受影响）。"""
    canvas = _make_canvas(monkeypatch)
    canvas._visual_pick()  # 无 seed URL → Toast 提示并返回
    assert len(canvas._fields_model.rows()) == 0
    assert not canvas._locked
    canvas.deleteLater()


# ── P4-2 AI 计划审核卡片（审核动作与 AI 解耦） ──


def _emit_ai_plan(canvas, draft=None):
    """模拟后台 worker 返回 AI 计划：连接信号后 emit（sender 守卫可过）。"""
    from omnicrawl.gui.views.task_canvas import _PlanReviewWorker

    worker = _PlanReviewWorker("https://example.org/news 采集新闻", canvas)
    worker.result_ready.connect(canvas._on_ai_plan_ready)
    worker.ai_unavailable.connect(canvas._on_ai_plan_unavailable)
    worker.ai_error.connect(canvas._on_ai_plan_error)
    canvas._plan_worker = worker
    worker.result_ready.emit(draft if draft is not None else _make_ai_draft("https://example.org/news"))
    return worker


def test_ai_plan_card_accept_applies_draft(monkeypatch):
    """AI 计划返回 → 卡片出现；「采纳计划」把 AI 草稿应用到画布并展开字段区。"""
    canvas = _make_canvas(monkeypatch)
    canvas._ai_plan_enabled = False  # 避免 _on_start 启动真实后台线程
    canvas._url_edit.setText("https://example.org/news")
    canvas._on_start()
    assert not canvas._plan_card.isVisibleTo(canvas)

    _emit_ai_plan(canvas)

    assert canvas._plan_card.isVisibleTo(canvas)
    assert "AI 计划已生成" in canvas._plan_title.text()
    assert not canvas._config.monitor_same_url  # 尚未采纳

    canvas._accept_plan()
    assert not canvas._plan_card.isVisibleTo(canvas)
    assert canvas._ai_plan_draft is None
    assert canvas._config.monitor_same_url is True  # AI 计划已应用
    assert not canvas._fields_section.collapsed()  # 展开字段区引导复核
    canvas.deleteLater()


def test_ai_plan_card_dismiss_keeps_local_draft(monkeypatch):
    """「忽略」AI 计划：保留本地草稿，仅收起卡片（审核动作与 AI 解耦）。"""
    canvas = _make_canvas(monkeypatch)
    canvas._ai_plan_enabled = False
    canvas._url_edit.setText("https://example.org/news")
    canvas._on_start()
    assert canvas._config.seed_urls == ["https://example.org/news"]

    _emit_ai_plan(canvas)
    assert canvas._plan_card.isVisibleTo(canvas)

    canvas._dismiss_plan()
    assert not canvas._plan_card.isVisibleTo(canvas)
    assert canvas._ai_plan_draft is None
    assert canvas._config.seed_urls == ["https://example.org/news"]  # 本地草稿原样保留
    assert not canvas._config.monitor_same_url
    canvas.deleteLater()


def test_plan_review_sender_guard_discards_stale_result(monkeypatch):
    """restart 后旧 worker 迟到结果被丢弃（sender 守卫），不污染新画布。"""
    canvas = _make_canvas(monkeypatch)
    canvas._ai_plan_enabled = False
    canvas._url_edit.setText("https://example.org/news")
    canvas._on_start()

    stale = _emit_ai_plan(canvas)
    canvas.restart()  # 废弃挂起计划（_plan_worker 置 None）
    stale.result_ready.emit(_make_ai_draft("https://example.org/news"))

    assert not canvas._plan_card.isVisibleTo(canvas)
    assert canvas._ai_plan_draft is None
    assert canvas._plan_worker is None
    canvas.deleteLater()


def test_plan_review_skipped_when_disabled(monkeypatch):
    """_ai_plan_enabled=False（无 AI 双轨）时不启动任何后台 worker。"""
    canvas = _make_canvas(monkeypatch)
    canvas._ai_plan_enabled = False
    canvas._url_edit.setText("https://example.org/news")
    canvas._on_start()
    assert canvas._plan_worker is None
    assert not canvas._plan_card.isVisibleTo(canvas)
    canvas.deleteLater()
