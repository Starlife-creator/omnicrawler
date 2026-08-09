"""从网站采集到 PDF 字段结果的统一桌面工作台。"""

from __future__ import annotations

import json
import queue
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any


def main() -> int:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except ImportError as exc:
        print(f"无法启动图形界面: {exc}")
        return 1

    from .. import __version__
    from ..core.config import load_config
    from ..pdfx.config import load_config as load_pdf_config
    from ..pdfx.database import Database
    from ..pdfx.desktop import open_path
    from ..pdfx.review import apply_review
    from ..pdfx.service import database_status, run_extraction, run_processing
    from ..pipeline import Pipeline
    from ..pipeline_ops.pdf_integration import ensure_pdf_project
    from ..pipeline_ops.provenance import write_pdf_source_manifest
    from ..state import StateStore

    class Workbench:
        def __init__(self, root: tk.Tk) -> None:
            self.root = root
            self.root.title(f"OmniCrawler {__version__} · 采集与 PDF 数据工作台")
            self.root.geometry("1080x720")
            self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
            self.stop_event = threading.Event()
            self.running = False
            self.crawl_config = tk.StringVar(value="configs/project.yaml")
            self.pdf_config = tk.StringVar(value="")
            self.review_file = tk.StringVar(value="")
            self.status_text = tk.StringVar(value="就绪")
            self._build()
            self.root.after(120, self._poll)

        def _build(self) -> None:
            outer = ttk.Frame(self.root, padding=12)
            outer.pack(fill="both", expand=True)
            title = ttk.Label(
                outer,
                text="全流程数据采集与 PDF 工作台",
                font=("TkDefaultFont", 16, "bold"),
            )
            title.pack(anchor="w", pady=(0, 10))

            project = ttk.LabelFrame(outer, text="项目文件", padding=10)
            project.pack(fill="x")
            self._path_row(project, 0, "采集项目 YAML", self.crawl_config, self._choose_crawl)
            self._path_row(project, 1, "PDF 项目 YAML", self.pdf_config, self._choose_pdf)
            self._path_row(project, 2, "人工复核文件", self.review_file, self._choose_review)
            project.columnconfigure(1, weight=1)

            flow = ttk.LabelFrame(outer, text="执行流程", padding=10)
            flow.pack(fill="x", pady=10)
            actions = [
                ("完整流程", self._full, "抓取 → 下载 → PDF 解析/OCR → 抽取 → 导出"),
                ("仅抓取", self._crawl_only, "不运行 PDF 后处理"),
                ("仅 PDF 处理", self._process_only, "解析、按需 OCR、导出 TXT/JSONL"),
                ("仅字段抽取", self._extract_only, "对已处理文档抽取、归一化和校验"),
                ("应用复核", self._apply_review, "回写人工确认或修正的字段"),
            ]
            for index, (label, command, hint) in enumerate(actions):
                ttk.Button(flow, text=label, command=command).grid(
                    row=0, column=index, padx=4, pady=2, sticky="ew"
                )
                ttk.Label(flow, text=hint, wraplength=180, justify="center").grid(
                    row=1, column=index, padx=4, sticky="n"
                )
                flow.columnconfigure(index, weight=1)

            controls = ttk.Frame(outer)
            controls.pack(fill="x")
            ttk.Button(controls, text="查看统一状态", command=self._status).pack(side="left")
            ttk.Button(controls, text="打开输出目录", command=self._open_output).pack(side="left", padx=6)
            ttk.Button(controls, text="停止", command=self._stop).pack(side="left")
            ttk.Label(controls, textvariable=self.status_text).pack(side="right")

            self.progress = ttk.Progressbar(outer, mode="indeterminate")
            self.progress.pack(fill="x", pady=(8, 4))
            log_box = ttk.LabelFrame(outer, text="运行日志", padding=6)
            log_box.pack(fill="both", expand=True)
            self.log = tk.Text(log_box, wrap="word", state="disabled", font=("TkFixedFont", 10))
            scroll = ttk.Scrollbar(log_box, orient="vertical", command=self.log.yview)
            self.log.configure(yscrollcommand=scroll.set)
            self.log.pack(side="left", fill="both", expand=True)
            scroll.pack(side="right", fill="y")

        def _path_row(self, parent, row: int, label: str, variable, command) -> None:
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
            ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=3)
            ttk.Button(parent, text="浏览…", command=command).grid(row=row, column=2, padx=(8, 0), pady=3)

        def _choose_crawl(self) -> None:
            value = filedialog.askopenfilename(filetypes=[("YAML", "*.yaml *.yml"), ("全部", "*")])
            if value:
                self.crawl_config.set(value)

        def _choose_pdf(self) -> None:
            value = filedialog.askopenfilename(filetypes=[("YAML", "*.yaml *.yml"), ("全部", "*")])
            if value:
                self.pdf_config.set(value)

        def _choose_review(self) -> None:
            value = filedialog.askopenfilename(filetypes=[("复核文件", "*.csv *.xlsx"), ("全部", "*")])
            if value:
                self.review_file.set(value)

        def _event(self, stage: str, value: dict[str, Any]) -> None:
            self.events.put(("log", {"stage": stage, "result": value}))

        def _resolved_pdf_project(self) -> Path:
            value = self.pdf_config.get().strip()
            if value:
                path = Path(value).expanduser().resolve()
                if not path.is_file():
                    raise FileNotFoundError(f"PDF 项目配置不存在: {path}")
                return path
            app = load_config(self.crawl_config.get())
            database_path = app.workspace / "state.sqlite3"
            if database_path.exists():
                with StateStore(database_path) as state:
                    write_pdf_source_manifest(app.workspace, state)
            path, _created = ensure_pdf_project(app)
            self.events.put(("pdf_config", str(path)))
            return path

        def _start(self, label: str, worker: Callable[[], Any]) -> None:
            if self.running:
                messagebox.showinfo("正在运行", "请先等待当前任务结束或点击停止。")
                return
            self.running = True
            self.stop_event.clear()
            self.status_text.set(f"正在运行: {label}")
            self.progress.start(12)

            def target() -> None:
                try:
                    result = worker()
                    self.events.put(("done", {"task": label, "result": result}))
                except Exception as exc:
                    self.events.put(("error", f"{type(exc).__name__}: {exc}"))

            threading.Thread(target=target, daemon=True).start()

        def _full(self) -> None:
            def worker():
                config = load_config(self.crawl_config.get())
                with Pipeline(config) as pipeline:
                    return pipeline.run(callback=self._event, should_stop=self.stop_event.is_set)
            self._start("完整流程", worker)

        def _crawl_only(self) -> None:
            def worker():
                config = load_config(self.crawl_config.get())
                with Pipeline(config) as pipeline:
                    return pipeline.run(
                        run_pdf=False,
                        callback=self._event,
                        should_stop=self.stop_event.is_set,
                    )
            self._start("仅抓取", worker)

        def _process_only(self) -> None:
            self._start(
                "仅 PDF 处理",
                lambda: run_processing(
                    self._resolved_pdf_project(),
                    callback=self._event,
                    should_stop=self.stop_event.is_set,
                ),
            )

        def _extract_only(self) -> None:
            self._start(
                "仅字段抽取",
                lambda: run_extraction(
                    self._resolved_pdf_project(),
                    auto_prepare=False,
                    callback=self._event,
                    should_stop=self.stop_event.is_set,
                ),
            )

        def _apply_review(self) -> None:
            def worker():
                review = Path(self.review_file.get()).expanduser().resolve()
                if not review.is_file():
                    raise FileNotFoundError(f"复核文件不存在: {review}")
                config = load_pdf_config(self._resolved_pdf_project())
                with Database(config.database) as database:
                    return apply_review(config, database, review)
            self._start("应用复核", worker)

        def _status(self) -> None:
            def worker():
                result: dict[str, Any] = {}
                app = load_config(self.crawl_config.get())
                crawl_db = app.workspace / "state.sqlite3"
                if crawl_db.exists():
                    with StateStore(crawl_db) as state:
                        result["crawl"] = {"latest_run": state.latest_run(), "totals": state.stats()}
                else:
                    result["crawl"] = {"status": "not_started"}
                try:
                    pdf = load_pdf_config(self._resolved_pdf_project())
                    with Database(pdf.database) as database:
                        result["pdf"] = database_status(database)
                except (FileNotFoundError, ValueError) as exc:
                    result["pdf"] = {"status": "not_started", "message": str(exc)}
                return result
            self._start("统一状态", worker)

        def _open_output(self) -> None:
            try:
                app = load_config(self.crawl_config.get())
                path = app.workspace / "output"
                path.mkdir(parents=True, exist_ok=True)
                open_path(path)
            except Exception as exc:
                messagebox.showerror("打开失败", f"{type(exc).__name__}: {exc}")

        def _stop(self) -> None:
            self.stop_event.set()
            self.status_text.set("已请求停止；当前原子阶段完成后退出")

        def _write_log(self, value: Any) -> None:
            line = json.dumps(value, ensure_ascii=False, indent=2, default=str)
            self.log.configure(state="normal")
            self.log.insert("end", line + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")

        def _poll(self) -> None:
            try:
                while True:
                    kind, value = self.events.get_nowait()
                    if kind == "pdf_config":
                        self.pdf_config.set(value)
                    elif kind == "log":
                        self._write_log(value)
                    elif kind == "done":
                        self._write_log(value)
                        self.running = False
                        self.progress.stop()
                        self.status_text.set("已完成")
                    elif kind == "error":
                        self._write_log({"error": value})
                        self.running = False
                        self.progress.stop()
                        self.status_text.set("运行失败")
                        messagebox.showerror("运行失败", value)
            except queue.Empty:
                pass
            self.root.after(120, self._poll)

    root = tk.Tk()
    Workbench(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
