"""PDF 工作台 — 扫描、解析、OCR、抽取一条龙。

Phase 2 落地：选目录 → 扫描 PDF → 选模板 → 异步执行全流程。
"""

from __future__ import annotations

import os
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..design_system import FONT_FAMILY_MONO, RADIUS, ThemeManager, scaled_font_px
from ..i18n import _
from .pdf_workbench_logic import _PDF_TEMPLATES
from .pdf_workbench_logic import (
    _collect_failures as _collect_failures,
)
from .pdf_workbench_result import PdfResultMixin
from .pdf_workbench_scan import PdfScanMixin
from .pdf_workbench_worker import _PdfPipelineWorker


# ── 视图 ──────────────────────────────────────────────────────────
class PdfWorkbenchView(PdfResultMixin, PdfScanMixin, QWidget):
    """PDF 批量处理工作台。

    状态机: idle → scanning → ready → running → done
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pending_request = ""
        self.setObjectName("pdfWorkbench")
        self.setAccessibleName(_("PDF 工作台"))
        # P1-4：启用拖放，支持拖入 PDF 文件或目录直接进入批量处理流程
        self.setAcceptDrops(True)

        self._state = "idle"          # idle | scanning | ready | running | done
        self._pdf_files: list[Path] = []
        self._worker: _PdfPipelineWorker | None = None
        self._temp_dir: str | None = None

        self._setup_ui()
        self._apply_style()
        ThemeManager.instance().theme_changed.connect(self._apply_style)

    # ── UI 搭建 ────────────────────────────────────────────────
    def set_pending_request(self, request: str) -> None:
        """接收首页自然语言入口带来的需求原文（§A-27）。

        以前首页把需求写进一个**无人读取**的 widget property，于是「请前往 PDF 工作台」
        这条指引是死胡同：用户跳过来后，系统不记得他说过什么。
        """
        self._pending_request = request.strip()
        status = getattr(self, "_scan_status", None)
        if status is not None and self._pending_request:
            status.setText(_("来自首页的需求：{0}").format(self._pending_request))

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        # 标题
        title = QLabel(_("PDF 工作台"))
        title.setObjectName("homeTitle")
        root.addWidget(title)

        subtitle = QLabel(_("选择 PDF 目录或拖入 PDF 文件，一键完成扫描 → 解析 → OCR → 抽取 → 导出全流程"))
        subtitle.setObjectName("sectionSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        # ── 区域 1: 目录选择 ──
        dir_group = QGroupBox(_("1. 选择 PDF 目录"))
        dir_layout = QVBoxLayout(dir_group)
        dir_layout.setSpacing(8)

        dir_row = QHBoxLayout()
        self._dir_input = QLineEdit()
        self._dir_input.setPlaceholderText(_("例如：D:\\合同文件\\2024"))
        self._dir_input.setReadOnly(False)
        dir_row.addWidget(self._dir_input, 1)

        browse_btn = QPushButton(_("浏览..."))
        browse_btn.clicked.connect(self._browse_dir)
        dir_row.addWidget(browse_btn)

        # P1-4：直接添加 PDF 文件（不限于整目录），与拖放共用暂存逻辑
        add_files_btn = QPushButton(_("添加文件..."))
        add_files_btn.clicked.connect(self._add_files)
        dir_row.addWidget(add_files_btn)

        self._scan_btn = QPushButton(_("扫描 PDF 文件"))
        self._scan_btn.setProperty("primary", True)
        self._scan_btn.clicked.connect(self._scan_directory)
        dir_row.addWidget(self._scan_btn)

        dir_layout.addLayout(dir_row)
        self._scan_status = QLabel("")
        self._scan_status.setObjectName("mutedLabel")
        dir_layout.addWidget(self._scan_status)
        root.addWidget(dir_group)

        # ── 区域 2: PDF 列表 + 模板选择 (QSplitter) ──
        content_splitter = QSplitter()
        content_splitter.setOrientation(Qt.Orientation.Horizontal)

        # 左栏: PDF 文件列表
        file_panel = QFrame()
        file_panel.setProperty("card", True)
        file_layout = QVBoxLayout(file_panel)
        file_layout.setContentsMargins(12, 12, 12, 12)

        file_header = QLabel(_("PDF 文件列表"))
        file_header.setObjectName("sectionSubtitle")
        file_layout.addWidget(file_header)

        self._file_list = QListWidget()
        self._file_list.setAlternatingRowColors(True)
        self._file_list.setMinimumWidth(250)
        file_layout.addWidget(self._file_list, 1)

        self._file_count_label = QLabel("")
        self._file_count_label.setObjectName("mutedLabel")
        file_layout.addWidget(self._file_count_label)

        content_splitter.addWidget(file_panel)

        # 右栏: 模板 + 选项
        config_panel = QFrame()
        config_panel.setProperty("card", True)
        config_layout = QVBoxLayout(config_panel)
        config_layout.setContentsMargins(12, 12, 12, 12)

        cfg_header = QLabel(_("2. 选择处理模板"))
        cfg_header.setObjectName("sectionSubtitle")
        config_layout.addWidget(cfg_header)

        self._template_combo = QComboBox()
        for _i, t in enumerate(_PDF_TEMPLATES):
            self._template_combo.addItem(f"{t['name']} — {t['desc']}", t["id"])
        config_layout.addWidget(self._template_combo)

        config_layout.addSpacing(12)

        # OCR 选项
        self._ocr_checkbox = QCheckBox(_("启用 OCR（扫描件/图片 PDF 建议开启）"))
        self._ocr_checkbox.setChecked(True)
        config_layout.addWidget(self._ocr_checkbox)

        config_layout.addSpacing(16)

        # 执行按钮
        btn_row = QHBoxLayout()
        self._execute_btn = QPushButton(_("▶ 开始处理"))
        self._execute_btn.setProperty("primary", True)
        self._execute_btn.setMinimumHeight(36)
        self._execute_btn.clicked.connect(self._execute)
        self._execute_btn.setEnabled(False)
        btn_row.addWidget(self._execute_btn, 1)

        self._cancel_btn = QPushButton(_("取消"))
        self._cancel_btn.clicked.connect(self._cancel)
        self._cancel_btn.setVisible(False)
        btn_row.addWidget(self._cancel_btn)

        config_layout.addLayout(btn_row)

        # 进度
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(False)
        config_layout.addWidget(self._progress_bar)

        self._stage_label = QLabel("")
        self._stage_label.setObjectName("mutedLabel")
        self._stage_label.setVisible(False)
        config_layout.addWidget(self._stage_label)

        config_layout.addStretch()

        content_splitter.addWidget(config_panel)
        content_splitter.setStretchFactor(0, 3)
        content_splitter.setStretchFactor(1, 2)
        root.addWidget(content_splitter, 1)

        # ── 区域 3: 结果输出 ──
        self._result_group = QGroupBox(_("处理结果"))
        self._result_group.setVisible(False)
        result_layout = QVBoxLayout(self._result_group)

        self._result_text = QTextEdit()
        self._result_text.setReadOnly(True)
        self._result_text.setMaximumHeight(150)
        result_layout.addWidget(self._result_text)

        result_btn_row = QHBoxLayout()
        open_output_btn = QPushButton(_("打开输出目录"))
        open_output_btn.clicked.connect(self._open_output_dir)
        result_btn_row.addWidget(open_output_btn)

        open_excel_btn = QPushButton(_("打开 Excel"))
        open_excel_btn.clicked.connect(self._open_excel)
        result_btn_row.addWidget(open_excel_btn)

        reset_btn = QPushButton(_("重新开始"))
        reset_btn.clicked.connect(self._reset)
        result_btn_row.addWidget(reset_btn)
        result_btn_row.addStretch()
        result_layout.addLayout(result_btn_row)

        root.addWidget(self._result_group)

    # ── 样式 ───────────────────────────────────────────────────
    def _apply_style(self, *_args: Any) -> None:
        t = ThemeManager.instance().tokens
        self.setStyleSheet(f"""
            QLabel#homeTitle {{
                font-size: {scaled_font_px("heading")}px;
                font-weight: 700;
                color: {t.text};
            }}
            QLabel#sectionSubtitle {{
                font-size: {scaled_font_px("body")}px;
                color: {t.text};
                font-weight: 600;
            }}
            QLabel#mutedLabel {{
                font-size: {scaled_font_px("small")}px;
                color: {t.muted};
            }}
            QListWidget {{
                border: 1px solid {t.border};
                border-radius: {RADIUS["sm"]}px;
                padding: 4px;
                background: {t.surface};
            }}
            QListWidget::item {{
                padding: 4px 8px;
                border-radius: 4px;
            }}
            QListWidget::item:selected {{
                background: {t.primary}22;
                color: {t.text};
            }}
            QTextEdit {{
                border: 1px solid {t.border};
                border-radius: {RADIUS["sm"]}px;
                padding: 8px;
                background: {t.surface};
                font-family: {FONT_FAMILY_MONO};
                font-size: {scaled_font_px("small")}px;
            }}
        """)

    # ── 目录浏览 ──────────────────────────────────────────────

    # ── P1-4：添加文件与拖放 ─────────────────────────────────


    # ── 拖放事件 ─────────────────────────────────────────────


    # ── 扫描目录 ──────────────────────────────────────────────


    # ── 执行 ───────────────────────────────────────────────────
    @Slot()
    def _execute(self) -> None:
        if self._state != "ready" or not self._pdf_files:
            return

        template_id = self._template_combo.currentData()
        run_ocr = self._ocr_checkbox.isChecked()
        input_dir = self._dir_input.text().strip()

        if run_ocr:
            # D14：OCR 勾选前检测 paddleocr 可用性，不可用则询问降级（避免整批 skipped 无解释）
            try:
                import paddleocr  # noqa: F401
            except ImportError:
                reply = QMessageBox.question(
                    self, _("OCR 依赖缺失"),
                    _("未检测到 PaddleOCR 依赖，OCR 功能不可用。\n" +

                      _("是否仍继续（本次仅规则抽取，不执行 OCR）？")),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                run_ocr = False

        self._state = "running"
        self._execute_btn.setVisible(False)
        self._cancel_btn.setVisible(True)
        self._scan_btn.setEnabled(False)
        self._template_combo.setEnabled(False)
        self._ocr_checkbox.setEnabled(False)
        self._result_group.setVisible(False)

        self._progress_bar.setVisible(True)
        self._progress_bar.setValue(0)
        self._stage_label.setVisible(True)
        self._stage_label.setText(_("正在准备..."))

        # C50：PDF 正文外发第三方 AI 前一次性确认（目标域名 + 预计文本量）
        ai_egress_ok = self._confirm_pdf_ai_egress()
        if ai_egress_ok:
            # 用户本次放行：清除上一次拒绝留下的进程级禁用标记，强制按当前配置桥接
            os.environ.pop("PDFX_LLM_PROVIDER", None)
        # 将 GUI 已配置的 AI 密钥桥接为 PDF 子系统所需的 PDFX_LLM_* 变量
        self._inject_pdf_llm_env()
        if not ai_egress_ok:
            # 用户拒绝外发 → 本次强制关闭 LLM，仅规则抽取
            os.environ["PDFX_LLM_PROVIDER"] = "disabled"

        # D18：持久工作目录——不再用临时目录（重跑复用 sqlite 增量续跑，结果不随系统清理丢失）
        import hashlib

        from ...core.runtime_paths import portable_data_root

        persistent_root = portable_data_root() / ".omnicrawler" / "pdf-workbench"
        try:
            persistent_root.mkdir(parents=True, exist_ok=True)
            task_key = hashlib.sha1(input_dir.encode("utf-8")).hexdigest()[:10]
            self._temp_dir = str(persistent_root / f"task-{task_key}")
            os.makedirs(self._temp_dir, exist_ok=True)
        except OSError:
            # 持久目录不可写时回退临时目录（功能不中断）
            self._temp_dir = tempfile.mkdtemp(prefix="omnicrawler_pdf_")
        work_dir = os.path.join(self._temp_dir, "work")
        output_dir = os.path.join(self._temp_dir, "output")
        config_path = os.path.join(self._temp_dir, "project.yaml")

        try:
            from omnicrawler.pdfx.project import create_project_config

            create_project_config(
                template_path=template_id,
                destination=config_path,
                project_name=_("PDF工作台任务"),
                input_dir=input_dir,
                work_dir=work_dir,
                output_dir=output_dir,
                ocr_backend="paddle" if run_ocr else "none",
            )
        except Exception as exc:
            self._on_failed(_(f"创建项目配置失败: {exc}"))
            return

        self._worker = _PdfPipelineWorker(
            config_path, run_ocr=run_ocr, parent=self
        )
        self._worker.stage_started.connect(self._on_stage_started)
        self._worker.stage_finished.connect(self._on_stage_finished)
        self._worker.warnings_received.connect(self._on_warnings)
        self._worker.progress.connect(self._progress_bar.setValue)
        self._worker.document_progress.connect(self._on_document_progress)
        self._worker.all_done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _confirm_pdf_ai_egress(self) -> bool:
        """C50：PDF 正文外发第三方 AI 前一次性确认。

        返回 True 表示放行（AI 未启用/本地模型/无密钥时无需确认直接放行）。
        """
        main = self.window()
        loader = getattr(main, "_load_ai_config_from_env", None)
        if loader is None:
            return True
        try:
            ai_config = loader()
        except Exception:
            return True
        provider = ai_config.get("providers", {}).get("default", {})
        if ai_config.get("mode") != "enabled" or not isinstance(provider, dict):
            return True  # AI 未启用无需确认
        # C37：隐私开关 — PDF 正文外发被禁用时直接拒绝 AI 外发，回退规则抽取
        try:
            from ...core.ai_env import load_ai_privacy

            privacy = load_ai_privacy(getattr(main, "_project_root", None))
        except Exception:
            privacy = {}
        if not privacy.get("allow_pdf_content", True):
            QMessageBox.information(
                self,
                _("PDF 内容 AI 已禁用"),
                _("按隐私设置，PDF 正文不会发送到任何 AI 服务。本次将仅使用本地规则抽取。"),
            )
            return False
        base_url = str(provider.get("base_url", "") or "")
        api_key = str(provider.get("api_key", "") or "")
        is_local = "127.0.0.1" in base_url or "localhost" in base_url
        if not api_key and not is_local:
            return True  # 无密钥不会实际外发
        char_count = sum(path.stat().st_size for path in self._pdf_files if path.is_file())
        host = urllib.parse.urlsplit(base_url).netloc or base_url
        reply = QMessageBox.question(
            self,
            _("PDF 内容外发确认"),
            _("将把 PDF 正文发送到外部 AI 服务：\n\n目标: {0}\n预计文本量: 约 {1} 字符\n\n" +

              _("仅在确认信任该服务后继续。拒绝后将使用规则抽取。")).format(host, char_count),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _inject_pdf_llm_env(self) -> None:
        """将 GUI 的 AI 配置桥接为 PDF 子系统期望的 PDFX_LLM_* 环境变量。

        根因：GUI 的 AI 服务中心写入 OMNICRAWL_AI_API_KEY，而 PDF 模板
        （generic_template.yaml 等）的 llm 段读取 PDFX_LLM_API_KEY /
        PDFX_LLM_PROVIDER / PDFX_LLM_BASE_URL / PDFX_LLM_MODEL。两者从未桥接，
        导致即便用户在 GUI 配置了密钥，PDF 抽取阶段的 LLM 也因
        provider 默认 disabled 且密钥为空而完全不生效。

        注：PDF 子系统的 llm.provider 仅接受 disabled / openai_compatible，
        因此这里把 GUI 的非 disabled 类型统一映射为 openai_compatible
        （兼容 OpenAI / Ollama / 自定义 OpenAI 风格端点）。
        """
        main = self.window()
        loader = getattr(main, "_load_ai_config_from_env", None)
        if loader is None:
            return
        try:
            ai_config = loader()
        except Exception:
            return
        provider = ai_config.get("providers", {}).get("default", {})
        if ai_config.get("mode") != "enabled" or not isinstance(provider, dict):
            return
        api_key = str(provider.get("api_key", "") or "")
        base_url = str(provider.get("base_url", "") or "")
        # C47：本地端点（Ollama 等）无需 API Key 也应桥接；云端缺 key 才跳过
        is_local = "127.0.0.1" in base_url or "localhost" in base_url
        if not api_key and not is_local:
            return

        gui_provider = str(provider.get("type", "openai_compatible"))
        pdf_provider = "openai_compatible" if gui_provider != "disabled" else "disabled"

        # D5：记录注入键，任务结束后从进程环境清除（减少密钥残留窗口）
        self._injected_keys = []
        injected: dict[str, str] = {}
        if not os.environ.get("PDFX_LLM_PROVIDER"):
            injected["PDFX_LLM_PROVIDER"] = pdf_provider
        if not os.environ.get("PDFX_LLM_API_KEY"):
            injected["PDFX_LLM_API_KEY"] = provider["api_key"]
        if not os.environ.get("PDFX_LLM_BASE_URL"):
            injected["PDFX_LLM_BASE_URL"] = provider.get("base_url", "")
        if not os.environ.get("PDFX_LLM_MODEL"):
            injected["PDFX_LLM_MODEL"] = provider.get("model", "")
        if not os.environ.get("PDFX_LLM_TIMEOUT"):
            injected["PDFX_LLM_TIMEOUT"] = str(provider.get("timeout_seconds", 60))
        for key, value in injected.items():
            os.environ[key] = value
            self._injected_keys.append(key)


    def _clear_injected_env(self) -> None:
        """D5：任务结束后清除本工作台注入的 PDFX_LLM_* 环境变量（减少密钥残留窗口）。"""
        for key in getattr(self, "_injected_keys", []):
            os.environ.pop(key, None)
        self._injected_keys = []


    # ── 取消 ───────────────────────────────────────────────────

    # ── 打开结果 ──────────────────────────────────────────────


    # ── 重置 ───────────────────────────────────────────────────
