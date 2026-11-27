"""CLI 命令分发注册表 — 把命令名映射到处理函数（单一分发表）。

``cli._main`` 只负责参数定义、日志与错误边界；全部子命令的执行逻辑
集中注册在本模块（命令名、参数、输出与退出码与 _main 中定义的解析器一一对应）。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..commands import capsule as cmd_capsule
from ..commands import components as cmd_components
from ..commands import field as cmd_field
from ..commands import init_project as cmd_init
from ..commands import plan as cmd_plan
from ..commands import queue as cmd_queue
from ..commands import recovery as cmd_recovery
from ..commands import run_status as cmd_status
from ..commands import run_task as cmd_run
from ..commands import schedule as cmd_schedule
from ..commands import security as cmd_security
from ..commands import task as cmd_task
from ..commands import template as cmd_template
from ..commands import transform as cmd_transform
from ..commands import worker as cmd_worker
from ..commands import workspace as cmd_workspace
from ..core.config import load_config, validate_config
from ..core.migrations import migrate_file
from ..pipeline import Pipeline, build_registry
from ..plugins.plugins import TrustPromptResult, set_default_trust_prompter
from ..services.doctor import run_doctor
from ..services.retention import apply_retention, plan_retention, serialize_plan
from ..services.server import serve
from ..state import StateStore

Handler = Callable[[argparse.Namespace], None]
_registry: dict[str, Handler] = {}


def _cli_trust_prompter(plugin_id: str, username: str, fingerprint: str) -> TrustPromptResult:
    """CLI 信任询问：仅 TTY 交互环境才询问，脚本/管道环境直接拒绝。"""
    if not sys.stdin.isatty():
        return TrustPromptResult.REJECT
    print(
        f"\n插件 {plugin_id} 的作者 {username}（指纹 {fingerprint}）不在本地信任列表。",
        file=sys.stderr,
    )
    print("1) 信任并加载（加入信任列表，以后自动信任该作者）", file=sys.stderr)
    print("2) 仅本次加载（不加入信任列表）", file=sys.stderr)
    print("3) 拒绝加载 [默认]", file=sys.stderr)
    try:
        choice = input("请选择 [1/2/3]: ").strip()
    except (EOFError, KeyboardInterrupt):
        return TrustPromptResult.REJECT
    if choice == "1":
        return TrustPromptResult.TRUST_AND_LOAD
    if choice == "2":
        return TrustPromptResult.LOAD_ONCE
    return TrustPromptResult.REJECT


set_default_trust_prompter(_cli_trust_prompter)


def _register(name: str) -> Callable[[Handler], Handler]:
    def _wrap(fn: Handler) -> Handler:
        _registry[name] = fn
        return fn

    return _wrap


def lookup(name: str) -> Handler | None:
    return _registry.get(name)


def _json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


# ── Simple commands (no config needed) ──────────────────────────


@_register("repair")
def _run_repair(args: argparse.Namespace) -> None:
    from ..services.repair_workflow import execute

    _json(execute(args.action, config_path=Path(args.config),
                  evidence=Path(args.evidence) if args.evidence else None,
                  candidate_path=Path(args.candidate) if args.candidate else None,
                  generate=args.generate, max_rounds=args.max_rounds))


@_register("analyze-archive")
def _run_archive_analysis(args: argparse.Namespace) -> None:
    from ..services.archive_analysis import execute

    _json(execute(Path(args.manifest), Path(args.output),
                  config_path=Path(args.ai_config) if args.ai_config else None, use_ai=args.ai))


@_register("pdf")
def _run_pdf_command(args: argparse.Namespace) -> None:
    """PDF 子命令：把参数原样转发给 PDF 子系统。

    转发而不是复制选项定义——复制就是第二个真源，必然与 pdfx 漂移。
    """
    from ._main import _dispatch_pdf

    extra = list(getattr(args, "pdf_args", None) or [])
    if getattr(args, "pdf_help", False):
        extra = ["--help", *extra]
    if not extra:
        extra = ["--help"]
    _dispatch_pdf(["pdf", *extra])


@_register("workbench")
def _run_workbench(args: argparse.Namespace) -> None:
    from ..services.workbench import main as workbench_main

    # 显式传空：工作台自己解析参数，不能让它读到 `omnicrawler` 的 argv
    # （否则 `omnicrawler workbench` 会被当成「未知参数」）。
    raise SystemExit(workbench_main([]))


@_register("field-suggest")
def _run_field_suggest(args: argparse.Namespace) -> None:
    _json(cmd_field.execute_field_suggest(
        args.html, output=args.output or "", limit=args.limit,
    ))


@_register("record-actions")
def _run_record_actions(args: argparse.Namespace) -> None:
    _json(
        cmd_field.execute_record_actions(
            args.url, args.output, timeout=args.timeout,
            save_session=not getattr(args, "no_save_session", False),
        )
    )


@_register("api-discover")
def _run_api_discover(args: argparse.Namespace) -> None:
    _json(cmd_field.execute_api_discover(args.input, args.output))


@_register("init")
def _run_init(args: argparse.Namespace) -> None:
    _json(cmd_init.execute(args.template, args.output, args.name))


@_register("wizard")
def _run_wizard(args: argparse.Namespace) -> None:
    from ._main import _wizard

    _wizard(Path(args.output))


@_register("plugins")
def _run_plugins(args: argparse.Namespace) -> None:
    command = getattr(args, "plugins_command", None)
    if command == "catalogs":
        # #77 Phase 1：多索引的增删查（每源独立验签；写回会重排 YAML，命令内会明示）
        from ..commands.plugins_catalog import (
            catalogs_add,
            catalogs_list,
            catalogs_remove,
        )

        config_path = getattr(args, "config", None)
        if not config_path:
            print("需要 --config/-c 指定配置文件")
            return
        action = getattr(args, "catalogs_action", None) or "list"
        url = str(getattr(args, "catalogs_url", None) or "").strip()
        if action == "list":
            payload, _code = catalogs_list(config_path=config_path)
        elif action == "add":
            payload, _code = catalogs_add(
                config_path=config_path,
                url=url,
                kind=str(getattr(args, "catalog_kind", "community")),
                trust=str(getattr(args, "catalog_trust", "") or ""),
                priority=int(getattr(args, "catalog_priority", 100)),
            )
        else:
            payload, _code = catalogs_remove(config_path=config_path, url=url)
        if str(getattr(args, "format", "text") or "text").lower() == "json":
            _json(payload)
        else:
            marker = "[OK]" if payload.get("status") == "ok" else "[FAIL]"
            print(f"{marker} {payload.get('detail', '')}")
            for source in payload.get("sources") or []:
                implicit = "（隐式官方源）" if source.get("implicit") else ""
                print(
                    f"  - {source.get('url', '')}  kind={source.get('kind', '')}"
                    f"  trust={source.get('trust', '')}  priority={source.get('priority', '')}"
                    f"  enabled={source.get('enabled', '')}{implicit}"
                )
        return
    if command == "scaffold-contract2":
        # Phase 3（P1 第 67 轮）：新建契约 2 工程骨架（非原地改造）
        from pathlib import Path as _ScaffoldPath

        from ..plugins.plugin_sdk import scaffold_contract2

        plugin_id = getattr(args, "plugin_id", None)
        if not plugin_id:
            _json({"ok": False, "error": "scaffold-contract2 需要 --plugin-id <id>"})
            raise SystemExit(2)
        display_name = getattr(args, "display_name", None) or ""
        output_dir = _ScaffoldPath(getattr(args, "output_dir", "."))
        try:
            root = scaffold_contract2(
                output_dir, plugin_id, display_name=display_name
            )
        except (ValueError, FileExistsError) as exc:
            _json({"ok": False, "error": str(exc)})
            raise SystemExit(2)
        _json({
            "ok": True,
            "plugin_dir": str(root),
            # ★ W3.5（2026-09-16，按 AUTHOR_GUIDE.md 实测）：工程生成在 `<output-dir>/<plugin-id>/`，
            #   而此前这里直接给 `--local .` ⇒ 与自己的 `plugin_dir` 字段**互相矛盾**：作者照抄
            #   就会审到**父目录**（只因 audit 会递归才发现得了，属于碰巧能用）。补一步 `cd` 后，
            #   提示与指南第 3 步的命令序列一字不差地对齐。
            "next": [
                f"cd {root}",
                "omnicrawler plugins audit --local .",
                "pytest -m plugin_contract",
            ],
        })
        raise SystemExit(0)
    review_target = getattr(args, "review", None)
    if review_target:
        # Phase 3（Q4/G3）：审核辅助分析（AI 增强审核员，纯静态证据非门禁）
        from pathlib import Path as _ReviewPath

        from ..plugins.plugin_review import review_analyze

        try:
            analysis = review_analyze(_ReviewPath(review_target))
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            _json({"ok": False, "error": f"分析失败: {exc}"})
            raise SystemExit(2)
        _json({"ok": True, "analysis": analysis.to_dict()})
        raise SystemExit(0)
    if command == "audit":
        # Phase 2a（B5/H4）：--report 优先生成脱敏环境诊断报告
        if getattr(args, "report", False):
            from ..plugins.plugin_audit import generate_environment_report

            report = generate_environment_report()
            # P2-2：默认仍是文本（人读）；`--format json` 时 stdout 才是纯 JSON。
            if str(getattr(args, "format", "text") or "text").lower() == "json":
                _json({"ok": True, "report": report})
            else:
                print(report)
            raise SystemExit(0)
        # Phase 2b（H4 第 66 轮④）：--export-egress 导出共现事件 JSONL（SIEM）
        egress_export = getattr(args, "export_egress", None)
        if egress_export:
            from pathlib import Path as _ExportPath

            from ..plugins.plugin_audit import export_egress_cooccurrence
            from ..state.state_store import StateStore

            config = load_config(args.config) if args.config else None
            if config is None:
                _json({"ok": False, "error": "--export-egress 需要 --config 指向工作区配置"})
                raise SystemExit(2)
            state = StateStore(config.workspace / "state.sqlite3")
            count = export_egress_cooccurrence(state, _ExportPath(egress_export))
            _json({"ok": True, "exported": count, "file": egress_export})
            raise SystemExit(0)
        # Phase 1（B5）：本地插件自检——许可+凭据，与 CI 门 2 同逻辑
        from pathlib import Path as _Path

        from ..plugins.plugin_audit import audit_local_directory

        local = getattr(args, "local", None)
        if not local:
            _json({"ok": False, "error": "plugins audit 需要 --local <dir> 指定插件目录"})
            raise SystemExit(2)
        results = audit_local_directory(_Path(local))
        if not results:
            _json({"ok": False, "error": f"未在 {local} 找到插件（需含 plugin.py 的目录）"})
            raise SystemExit(2)
        payload = {"ok": all(r.ok for r in results), "audited": [r.to_dict() for r in results]}
        _json(payload)
        raise SystemExit(0 if payload["ok"] else 1)
    config = load_config(args.config) if args.config else None
    _json(build_registry(config).describe())


@_register("templates")
def _run_templates(args: argparse.Namespace) -> None:
    result = cmd_template.execute(
        args.templates_command,
        query=getattr(args, 'query', ''), category=getattr(args, 'category', ''),
        tags=getattr(args, 'tag', None) or [], capabilities=getattr(args, 'capability', None) or [],
        url=getattr(args, 'url_option', '') or getattr(args, 'url', ''),
        headers=getattr(args, 'header', None) or [],
        body_file=getattr(args, 'body_file', ''), json_file=getattr(args, 'json_file', ''),
        limit=getattr(args, 'limit', 5),
        template_id=getattr(args, 'template_id', ''), sets=getattr(args, 'set', None) or [],
        output=getattr(args, 'output', ''), force=getattr(args, 'force', False),
        include_legacy=getattr(args, 'include_legacy', False),
        pack=getattr(args, 'pack', ''), overwrite=getattr(args, 'overwrite', False),
        target=getattr(args, 'target', ''),
        timeout=getattr(args, 'timeout', 20.0),
        before=getattr(args, 'before', ''), after=getattr(args, 'after', ''),
        base=getattr(args, 'base', ''), user=getattr(args, 'user', ''), update=getattr(args, 'update', ''),
        config=getattr(args, 'config', ''), acceptance=getattr(args, 'acceptance', ''),
        parameter_file=getattr(args, 'parameters', ''),
        expected=getattr(args, 'expected', ''),
    )
    if isinstance(result, dict) and result.get("ok") is False:
        _json(result)
        raise SystemExit(1)
    _json(result)
    if args.templates_command == "validate":
        raise SystemExit(0 if result.get("ok") else 1)


@_register("schedule")
def _run_schedule(args: argparse.Namespace) -> None:
    _json(cmd_schedule.execute(
        args.schedule_command,
        database=getattr(args, 'database', ''),
        name=getattr(args, 'name', ''), config_path=getattr(args, 'config', ''),
        every_seconds=getattr(args, 'every_seconds', 0),
        require_ac=getattr(args, 'require_ac', False), require_network=getattr(args, 'require_network', False),
        minimum_battery=getattr(args, 'minimum_battery', None),
        limit=getattr(args, 'limit', 1),
    ))


@_register("migrate")
def _run_migrate(args: argparse.Namespace) -> None:
    target, notes = migrate_file(Path(args.config), Path(args.output), overwrite=args.force)
    _json({"created": str(target), "notes": notes, "rollback": "保留原配置并重新指向原文件"})


@_register("capabilities")
def _run_capabilities(args: argparse.Namespace) -> None:
    from ..core.capabilities import capability_report, runtime_self_test

    report = capability_report(
        verify_imports=args.verify_imports or args.self_test,
        portable_paths=args.portable_paths,
        mode=args.mode,
        require_features=args.require,
    )
    if args.self_test:
        report["self_test"] = runtime_self_test()
        report["ok"] = report["ok"] and report["self_test"]["ok"]
    _json(report)
    raise SystemExit(0 if report["ok"] else 1)


@_register("runtime-verify")
def _run_runtime_verify(args: argparse.Namespace) -> None:
    from ..core.runtime_manifest import verify_runtime_manifest

    report = verify_runtime_manifest(Path(args.root))
    _json(report)
    raise SystemExit(0 if report["ok"] else 1)


@_register("import-easyspider")
def _run_import_easyspider(args: argparse.Namespace) -> None:
    from ..sources.easyspider_bridge import EasySpiderImporter, import_easyspider
    if getattr(args, "ir", False):
        # S2.5.23：--ir 输出 Task IR JSON，不再静默 no-op
        import json as _json
        from pathlib import Path as _Path

        ir = EasySpiderImporter(args.json).to_task_ir()
        output = _json.dumps(ir, ensure_ascii=False, indent=2)
        if args.output:
            _Path(args.output).write_text(output, encoding="utf-8")
        else:
            print(output)
        return
    config = import_easyspider(args.json, output_path=args.output)
    if not args.output:
        # P2-2：默认仍是 YAML（既有行为不变），但形态可显式选择，避免被当作 JSON 解析。
        if str(getattr(args, "format", "yaml") or "yaml").lower() == "json":
            print(_json.dumps(config, ensure_ascii=False, indent=2))
        else:
            import yaml as _yaml
            print(_yaml.dump(config, allow_unicode=True, default_flow_style=False, sort_keys=False))


@_register("visual-select")
def _run_visual_select(args: argparse.Namespace) -> None:
    import sys as _sys

    from ..visual_selector.server import main as vs_main
    _saved = _sys.argv[:]
    try:
        _sys.argv = ["visual-select"]
        if hasattr(args, "port"):
            _sys.argv.extend(["--port", str(args.port)])
        if hasattr(args, "output") and args.output:
            _sys.argv.extend(["--output", str(args.output)])
        vs_main()
    finally:
        _sys.argv = _saved


@_register("auto-analyze")
def _run_auto_analyze(args: argparse.Namespace) -> None:
    import sys as _sys

    from ..extraction.intelligent_scraper import main as is_main
    _saved = _sys.argv[:]
    try:
        _sys.argv = ["auto-analyze"]
        _sys.argv.append(args.input)
        if hasattr(args, "output") and args.output:
            _sys.argv.extend(["-o", str(args.output)])
        if hasattr(args, "url") and args.url:
            _sys.argv.extend(["--url", str(args.url)])
        if getattr(args, "always_browser", False):
            _sys.argv.append("--always-browser")
        if getattr(args, "item_path", ""):
            _sys.argv.extend(["--item-path", str(args.item_path)])
        settings = None
        if getattr(args, "request", ""):
            from ..commands.task import compile_request

            # ★ 2026-11-27 修：比较对象必须是**被分析的网址**，不是 `args.input`。
            #
            #   `auto-analyze` 支持「本地 HTML 文件 + `--url` 指定原始网址」
            #   （离线夹具、抓下来再分析的常见用法）。旧实现拿需求里的网址去比
            #   `args.input`（那是文件路径），于是**只要用文件输入就一定报**
            #   「需求中的入口网址与分析网址不一致」—— 而用户什么都没做错，
            #   提示里的修复建议（去 `source.seeds` 补网址）也无从下手。
            settings = compile_request(
                args.request, fallback_url=str(getattr(args, "url", "") or args.input)
            )["task"]
            analysis_url = str(getattr(args, "url", "") or args.input)
            if settings["url"] != analysis_url:
                raise ValueError(
                    "需求中的入口网址与分析网址不一致："
                    f"需求写的是 {settings['url']}，本次分析的是 {analysis_url}"
                )
        if settings is None:
            is_main()
        else:
            is_main(task_settings=settings)
    finally:
        _sys.argv = _saved


@_register("c4a-fetch")
def _run_c4a_fetch(args: argparse.Namespace) -> None:
    import sys as _sys

    from ..sources.crawl4ai_bridge import main as c4a_main
    _saved = _sys.argv[:]
    try:
        _sys.argv = ["c4a-fetch", args.url]
        if args.stealth:
            _sys.argv.append("--stealth")
        if args.extract:
            _sys.argv.extend(["--extract", str(args.extract)])
        if args.output:
            _sys.argv.extend(["--output", str(args.output)])
        c4a_main()
    finally:
        _sys.argv = _saved


@_register("stealth-fingerprint")
def _run_stealth_fingerprint(args: argparse.Namespace) -> None:
    import sys as _sys

    from ..fetching.stealth_enhanced import main as sf_main
    _saved = _sys.argv[:]
    try:
        _sys.argv = ["stealth-fingerprint", "--count", str(getattr(args, "count", 1))]
        if getattr(args, "json", False):
            _sys.argv.append("--json")
        sf_main()
    finally:
        _sys.argv = _saved


@_register("gen-templates")
def _run_gen_templates(args: argparse.Namespace) -> None:
    import sys as _sys

    from ..templates.apify_templates import main as gt_main
    _saved = _sys.argv[:]
    try:
        _sys.argv = ["gen-templates"]
        if hasattr(args, "list") and args.list:
            _sys.argv.append("--list")
        if hasattr(args, "generate") and args.generate:
            _sys.argv.extend(["--generate", str(args.generate)])
        if hasattr(args, "all") and args.all:
            _sys.argv.extend(["--all", str(args.all)])
        gt_main()
    finally:
        _sys.argv = _saved


@_register("browser-kernels")
def _run_browser_kernels(args: argparse.Namespace) -> None:
    """列出浏览器内核（只读，不启动浏览器、不联网）。

    这是 §15.16.7「主界面」的数据源。GUI 与 CLI 共用它，
    避免界面自己再推导一遍「哪个内核可用」而与槽位判断漂移。

    本地导入的内核也会出现在同一张表里 —— **隐藏状态比不做还糟**。
    """
    import json
    from pathlib import Path

    from ..services.browser_kernels import describe_kernels, list_kernels

    # `--local` 支持 `<路径>` 与 `<标签>=<路径>`
    local_entries: list[dict[str, str]] = []
    for raw in getattr(args, "local", None) or []:
        text = str(raw)
        if "=" in text:
            label, _, path_text = text.partition("=")
        else:
            label, path_text = "", text
        local_entries.append({"label": str(label), "path": str(path_text)})

    catalog_data = None
    catalog_path = str(getattr(args, "catalog", "") or "").strip()
    if catalog_path:
        try:
            catalog_data = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"无法读取 catalog：{exc}") from exc

    # 本地内核**探测版本**：驱动主版本必须与浏览器主版本一致，
    # 这条约束不能靠代码抹平，只能如实提前告知（§15.16.6）。
    from ..services.browser_kernel_probe import probe_kernel

    probes = [probe_kernel(entry["path"]) for entry in local_entries]

    options = list_kernels(catalog=catalog_data, local_kernels=local_entries)
    if getattr(args, "json_output", False):
        _json(
            {
                "kernels": [option.as_dict() for option in options],
                "local_probes": [probe.as_dict() for probe in probes],
            }
        )
        return
    print(describe_kernels(options))
    for probe in probes:
        if not probe.version:
            continue
        print(
            f"\n本地内核探测：{probe.path.name}"
            f"  版本 {probe.version}（{probe.detected_by}）"
        )
        hint = probe.driver_hint()
        if hint:
            print(f"  ⚠ {hint}")
        for warning in probe.warnings:
            print(f"  ⚠ {warning}")


@_register("components")
def _run_components(args: argparse.Namespace) -> None:
    from ..core.safe_action import require_explicit_apply

    # recipe 两个动作走独立入口：它们不碰 ComponentManager 的 zip 整包流程。
    if args.action in {"inspect-recipe", "install-recipe"}:
        _run_component_recipe(args)
        return

    # Wheel-Pack 组件执行：隔离子进程。
    if args.action == "run":
        _run_component_in_subprocess(args)
        return

    if args.action in {"uninstall", "rollback"}:
        require_explicit_apply(f"components {args.action}")
    result = cmd_components.execute(
        args.action, package=args.package or "", name=args.name or "",
        allow_unsigned=bool(args.allow_unsigned), sha256=args.sha256 or "",
    )
    # `list` 额外报**总占用**：维护者要求「不设上限，但要让用户看到总共占多少」。
    # 统计的是已装目录的真实字节数，不是下载量估算。
    if args.action == "list" and isinstance(result, dict):
        from ..core.runtime_paths import portable_data_root
        from ..services.component_install import components_size_summary

        root = portable_data_root() / "components"
        try:
            result["size_summary"] = components_size_summary(root)
        except OSError:
            pass
    _json(result)


def _run_component_in_subprocess(args: argparse.Namespace) -> None:
    """在隔离子进程里运行 Wheel-Pack 组件。

    **为什么要子进程**：`paddle` / `cv2` / `scipy` 与宿主已加载的
    `PySide6` / `lxml` 同进程时极易触发原生库符号冲突；Linux 下部分库
    还要求进程启动前设环境变量。只在该子进程内挂载，宿主零污染。

    **启动前重新校验**：组件目录可能在上次校验之后被改动，子进程一旦
    起来就没有拦截机会，所以每次运行前按 `component.json` 重算哈希。

    摘要经 `--payload` 传入（JSON 字符串或 `@文件`），组件从 stdin 读、
    向 stdout 写一个 JSON 对象——与既有 `ocr-file-v1` 同形的 JSON-over-stdio。
    """
    import json
    from pathlib import Path

    from ..services.component_runner import (
        ComponentRunnerError,
        build_native_spec,
        build_wheelpack_spec,
        describe_spec,
        run_component,
        run_native_component,
        verify_component_files,
    )

    component_dir = str(getattr(args, "component_dir", "") or "").strip()
    if not component_dir:
        raise SystemExit("components run 需要 --component-dir <已装配组件目录>")
    root = Path(component_dir).expanduser()
    if not root.is_dir():
        raise SystemExit(f"组件目录不存在：{root}")

    raw_payload = str(getattr(args, "payload", "") or "").strip()
    if raw_payload.startswith("@"):
        payload_file = Path(raw_payload[1:]).expanduser()
        if not payload_file.is_file():
            raise SystemExit(f"payload 文件不存在：{payload_file}")
        raw_payload = payload_file.read_text(encoding="utf-8")
    try:
        payload = json.loads(raw_payload) if raw_payload else {}
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--payload 不是合法 JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("--payload 顶层必须是 JSON 对象")

    try:
        # **按组件形态分流**：`native`（浏览器内核）直接 exec 自带可执行文件，
        # 没有 site-packages；`wheel-pack` 走宿主 worker 挂载 site-packages。
        # 早先这里只有 wheel-pack 一条路，native 组件在运行阶段报
        # 「组件缺少 site-packages 目录」（真实 CI 踩到）。
        entry_file = root / "entry.json"
        entry_data = (
            json.loads(entry_file.read_text(encoding="utf-8"))
            if entry_file.is_file()
            else {}
        )
        runner_kind = str(entry_data.get("runner") or "")
        if runner_kind == "native":
            native_spec = build_native_spec(root, entry_data)
            checked = verify_component_files(native_spec.component_root)
            if not getattr(args, "json_output", False):
                print(f"组件：{native_spec.name} {native_spec.version}".rstrip())
                print(f"引擎：{native_spec.engine}（native）")
                print(f"入口：{native_spec.executable}")
                print(f"已校验文件：{checked} 个")
            result = run_native_component(native_spec, payload)
        else:
            spec = build_wheelpack_spec(root)
            checked = verify_component_files(spec.component_root)
            if not getattr(args, "json_output", False):
                print(describe_spec(spec))
                print(f"已校验文件：{checked} 个")
            result = run_component(spec, payload)
    except ComponentRunnerError as exc:
        raise SystemExit(str(exc)) from exc

    _json(result)


def _run_component_recipe(args: argparse.Namespace) -> None:
    """recipe 装配的两个动作。

    **`install-recipe` 默认不下载**：从上游拉字节是显式的对外动作，
    与 `uninstall`/`rollback` 同一条纪律——必须 `--download` 才联网。
    不带标志时只做声明校验并打印将拉取的构件清单，让用户先看清再决定。
    """
    from ..services.component_install import (
        RecipeInstallError,
        describe_recipe,
        dumps_summary,
        install_recipe_file,
    )

    recipe = str(getattr(args, "recipe", "") or "").strip()
    if not recipe:
        raise SystemExit("components 需要 --recipe <recipe.json>")

    if args.action == "inspect-recipe":
        try:
            _json(describe_recipe(recipe))
        except RecipeInstallError as exc:
            raise SystemExit(str(exc)) from exc
        return

    component_root = str(getattr(args, "component_root", "") or "").strip()
    if not component_root:
        from ..core.runtime_paths import portable_data_root

        component_root = str(portable_data_root() / "components")

    if not getattr(args, "download", False):
        # 不给 --download：只校验 + 展示将拉什么，一个字节都不写。
        # 展示**两个数**：下载量与解压后磁盘占用。只给前者会形成虚假安全感
        # （`ocr-paddle` Linux 下载 322 MB，解压后约 600–700 MB）。
        try:
            plan = describe_recipe(recipe)
        except RecipeInstallError as exc:
            raise SystemExit(str(exc)) from exc
        from ..services.component_install import format_bytes

        total = int(plan.get("declared_total_bytes") or 0)
        installed = int(plan.get("estimated_installed_bytes") or 0)
        # **两个数都要给**：只报下载量会形成虚假安全感 ——
        # `.whl` 是 ZIP，原生扩展解压后约为下载量的 2 倍。
        print(
            f"将安装组件 {plan['name']} {plan['version']}，"
            f"需拉取 {len(plan['artifacts'])} 个构件。"
        )
        print(f"  下载量：{format_bytes(total)}（{total} 字节）")
        print(f"  磁盘占用：约 {format_bytes(installed)}（解压后，估算）")
        for item in plan["artifacts"]:
            # 内置构件没有 url（字节随包），显示来源而不是空行。
            if str(item.get("source") or "remote").lower() == "builtin":
                print(f"  - 内置（随包）：{item.get('builtin_path') or '（未声明路径）'}")
            else:
                print(f"  - {item['url']}")
        print("\n未加 --download，未做任何下载。确认无误后加 --download 执行。")
        return

    from ..core.config import DEFAULTS, AppConfig, deep_merge
    from ..security.egress import EgressBroker

    # 复用市场面板同一套出口策略：只限制私网目标、**强制开启审计**。
    # 第三方构件下载必须留痕——这一条不交给用户配置决定。
    raw = deep_merge(dict(DEFAULTS), {"egress": {"audit": True}})
    base = Path(Path(component_root).anchor or ".")
    raw.setdefault("project", {"name": "component-recipe", "workspace": str(base)})
    config = AppConfig(base, base, raw, base)

    try:
        summary = install_recipe_file(
            recipe, component_root, egress=EgressBroker(config)
        )
    except RecipeInstallError as exc:
        raise SystemExit(str(exc)) from exc
    print(dumps_summary(summary))


@_register("models")
def _run_models(args: argparse.Namespace) -> None:
    """模型上游下载：``probe`` 只读；``download`` 联网拉权重。

    退出码（便于脚本/agent 面判断，不靠解析文案）：
    0 就绪/成功 · 3 本机无 PaddleOCR 运行时（预期降级）· 4 下载失败。
    """
    from pathlib import Path

    from ..services import model_download

    if args.action == "probe":
        report = model_download.probe()
        _json(report)
        return

    cache_dir = Path(args.cache_dir) if args.cache_dir else None
    report = model_download.download(args.source or None, cache_dir=cache_dir)
    _json(report)
    status = str(report.get("status", ""))
    if status == "cancelled":
        return
    if status == "unavailable":
        raise SystemExit(3)
    if status == "failed":
        raise SystemExit(4)


@_register("self-update")
def _run_self_update(args: argparse.Namespace) -> None:
    """应用自更新：``check`` 只读；``apply`` 覆盖应用文件 ⇒ 破坏性，先过显式确认。

    退出码四态（便于脚本/agent 面判断，不靠解析文案）：
    0 已最新 · 1 有可用更新 · 2 未配置信任根或更新源（fail-closed 禁用）· 3 失败。
    """
    from ..commands import self_update as cmd_self_update
    from ..core.safe_action import require_explicit_apply

    if args.self_update_command == "apply" and not args.dry_run:
        # 与 components uninstall 同一判据：argv 里没有 --yes/--apply 就一个字节都不写。
        require_explicit_apply("self-update apply")

    if args.self_update_command == "cleanup":
        payload, code = cmd_self_update.cleanup(app_root=None, yes=bool(args.confirm))
    elif args.self_update_command == "ignore":
        payload, code = cmd_self_update.ignore(
            config_path=args.config, clear=bool(getattr(args, "clear", False))
        )
    elif args.self_update_command == "apply":
        payload, code = cmd_self_update.apply(
            config_path=args.config, platform=args.platform or "", edition=args.edition or "",
            package=args.package or "", dry_run=bool(args.dry_run),
            full=bool(getattr(args, "full", False)),
            to_versions=bool(getattr(args, "to_versions", False)),
        )
    else:
        payload, code = cmd_self_update.check(
            config_path=args.config, platform=args.platform or "", edition=args.edition or "",
        )

    if getattr(args, "json_output", False):
        _json(payload)
    else:
        print(f"[{payload.get('status')}] {payload.get('detail')}")
        notes = str(payload.get("notes") or "").strip()
        if notes:
            print(notes)
    raise SystemExit(code)


# ── Commands requiring config ───────────────────────────────────
# 与旧分发路径一致：先 load_config 校验配置，再执行命令，
# 保证配置错误的报错内容与退出码不变。


@_register("preflight")
def _run_preflight(args: argparse.Namespace) -> None:
    from ..pipeline_ops.preflight import run_preflight

    config = load_config(args.config)
    report = run_preflight(config)
    _json(report)
    raise SystemExit(0 if report["ok"] else 1)


@_register("sample")
def _run_sample(args: argparse.Namespace) -> None:
    from ..services.application_service import ApplicationService

    load_config(args.config)
    _json(ApplicationService(args.config).sample(pages=args.pages))


@_register("control")
def _run_control(args: argparse.Namespace) -> None:
    from ..services.application_service import ApplicationService

    load_config(args.config)
    service = ApplicationService(args.config)
    actions = {
        "status": lambda: service.query()["run"],
        "pause": service.pause,
        "resume": service.resume,
        "stop": service.stop,
    }
    _json(actions[args.action]())


@_register("security-report")
def _run_security_report(args: argparse.Namespace) -> None:
    load_config(args.config)
    _json(cmd_security.execute(args.config))


@_register("worker")
def _run_worker(args: argparse.Namespace) -> None:
    load_config(args.config)
    _json(cmd_worker.execute(args.config, args.action, session=args.session or ""))


@_register("queue")
def _run_queue(args: argparse.Namespace) -> None:
    # 嵌套子命令：config 仅 submit 携带，其余参数按需 getattr 兜底
    _json(cmd_queue.execute(
        getattr(args, "action", ""),
        config=getattr(args, "config", ""),
        redis_url=getattr(args, "redis_url", None),
        local_path=getattr(args, "local_path", None),
        worker_id=getattr(args, "worker_id", ""),
        interval=getattr(args, "interval", 1.0),
        max_tasks=getattr(args, "max_tasks", None),
        executor=getattr(args, "executor", "backend"),
    ))


@_register("scene")
def _run_scene(args: argparse.Namespace) -> None:
    from ..commands import scene as cmd_scene

    _json(cmd_scene.execute(
        getattr(args, "scene_command", ""),
        config=getattr(args, "config", ""),
        scene=getattr(args, "scene", ""),
        path=getattr(args, "path", ""),
        candidate_id=getattr(args, "candidate_id", 0),
        limit=getattr(args, "limit", 100),
        pending_only=bool(getattr(args, "pending", False)),
        accepted_only=bool(getattr(args, "accepted", False)),
        min_fitness=getattr(args, "min_fitness", 0.2),
        min_trials=getattr(args, "min_trials", 3),
        apply=bool(getattr(args, "apply", False)),
    ))


@_register("timeline")
def _run_timeline(args: argparse.Namespace) -> None:
    _json(cmd_capsule.timeline(
        args.config, run_id=args.run, capsule_dir=args.capsule_dir, limit=args.limit,
    ))


@_register("replay")
def _run_replay(args: argparse.Namespace) -> None:
    _json(cmd_capsule.replay(
        args.config, run_id=args.run, field=args.field,
        stage=args.stage, capsule_dir=args.capsule_dir, timeout=args.timeout,
        record_index=args.record_index, response_id=args.response_id, capsule_id=args.capsule_id,
    ))


@_register("task")
def _run_desire(args: argparse.Namespace) -> None:
    """走查 R3.3：把中文需求解析接到 CLI（解析器此前只有 GUI 可达）。"""
    _json(cmd_task.compile_request(args.request, fallback_url=args.fallback_url))


@_register("transform")
def _run_transform(args: argparse.Namespace) -> None:
    result = cmd_transform.execute(
        args.source,
        args.target,
        maps=args.map,
        transform_steps=args.transform_steps,
        sorts=args.sort,
        group_by=args.group_by,
        aggregations=args.agg,
        src_format=args.src_format,
        dst_format=args.dst_format,
        dry_run=args.dry_run,
        confirm=args.confirm,
        batch_size=args.batch_size,
        max_records=args.max_records,
        on_error=args.on_error,
        preview_limit=args.preview_limit,
    )
    _json(result)
    # 走查 R2.2 / R5.1：**"没起作用"必须落到用户的终端上**，不能只躺在 JSON 里。
    # 两条都会静默产生"看起来成功了"的结果：变换没生效、以及文本列被拿去排序/求和。
    for key in ("notice_ineffective", "notice_numeric_text"):
        notice = result.get(key)
        if notice:
            print(f"\n⚠ {notice}", file=sys.stderr)
    summary = result.get("post_processing_summary")
    if summary:
        print(f"\nℹ {summary}", file=sys.stderr)


@_register("workspace")
def _run_workspace(args: argparse.Namespace) -> None:
    from ..core.safe_action import require_explicit_apply

    if args.action == "rollback":
        require_explicit_apply("workspace rollback")
    if args.action != "import":
        if not args.config:
            raise ValueError("当前工作区操作必须提供--config")
        load_config(args.config)
    _json(cmd_workspace.execute(args.config or "", args.action, target=args.target or "", kind=args.kind,
                                destination=getattr(args, "destination", "") or ""))


@_register("plan")
def _run_plan(args: argparse.Namespace) -> None:
    load_config(args.config)
    _json(cmd_plan.execute(args.config, compare=args.compare or "", output=args.output or ""))


@_register("recovery")
def _run_recovery(args: argparse.Namespace) -> None:
    from ..core.safe_action import require_explicit_apply

    if args.action == "rollback-config":
        require_explicit_apply("recovery rollback-config")
    load_config(args.config)
    fingerprints = getattr(args, "fingerprint", None)
    if fingerprints and args.action not in {"retry-failed", "retry-discovery"}:
        raise ValueError("fingerprint 仅用于 retry-failed / retry-discovery")
    if args.run_id and args.action not in {"coverage", "retry-discovery"} or args.offset and args.action != "coverage":
        raise ValueError("run-id / offset 仅用于发现清单与发现重试")
    _json(cmd_recovery.execute(args.config, args.action, limit=args.limit, backup=args.backup or "",
                              fingerprints=fingerprints, run_id=args.run_id, offset=args.offset))


@_register("compare-runs")
def _run_compare_runs(args: argparse.Namespace) -> None:
    from ..services.document_facade import compare_runs

    config = load_config(args.config)
    with StateStore(config.workspace / "state.sqlite3") as state:
        report = compare_runs(state, args.before_run, args.after_run)
    if args.output:
        target = Path(args.output).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report["output"] = str(target)
    _json(report)


@_register("regression")
def _run_regression(args: argparse.Namespace) -> None:
    from ..services.regression_library import verify_regression_fixtures

    config = load_config(args.config)
    report = verify_regression_fixtures(config)
    _json(report)
    raise SystemExit(0 if report["ok"] else 1)


@_register("research-package")
def _run_research_package(args: argparse.Namespace) -> None:
    from ..services.research_package import create_research_package

    config = load_config(args.config)
    target = Path(args.output).expanduser().resolve()
    _json(create_research_package(config, target, include_raw=args.include_raw))


@_register("backup")
def _run_backup(args: argparse.Namespace) -> None:
    if args.backup_command == "restore":
        from ..services.research_package import restore_package

        _json(restore_package(Path(args.package), Path(args.target)))
        return
    from ..services.research_package import create_backup

    config = load_config(args.config)
    target = Path(args.output).expanduser().resolve()
    _json(create_backup(config, target, include_raw=args.include_raw))


@_register("cleanup")
def _run_cleanup(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    candidates = plan_retention(config)
    if args.apply:
        _json(apply_retention(config, candidates))
    else:
        _json({"dry_run": True, "candidates": serialize_plan(candidates)})


@_register("validate")
def _run_validate(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    errors, warnings = validate_config(config)
    _json({"ok": not errors, "errors": errors, "warnings": warnings, "workspace": str(config.workspace)})
    raise SystemExit(1 if errors else 0)


@_register("doctor")
def _run_doctor(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    report = run_doctor(config)
    _json(report)
    raise SystemExit(0 if report["ok"] else 1)


@_register("run")
@_register("resume")
def _run_run_or_resume(args: argparse.Namespace) -> None:
    load_config(args.config)
    result = cmd_run.execute(
        args.config, args.command,
        max_pages=args.max_pages,
        retry_failed=bool(getattr(args, "retry_failed", False)),
        progress=bool(getattr(args, "progress", False)),
        strict=bool(getattr(args, "strict", False)),
    )
    _json(result)
    raise SystemExit(int(result.get("exit_code", 0)))


@_register("status")
def _run_status(args: argparse.Namespace) -> None:
    load_config(args.config)
    _json(cmd_status.execute(args.config, output_format=getattr(args, "format", "json")))


@_register("export")
def _run_export(args: argparse.Namespace) -> None:
    from ..services.application_service import ApplicationService

    load_config(args.config)
    _json(ApplicationService(args.config).export(args.run_id))


@_register("reprocess")
def _run_reprocess(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    with Pipeline(config) as pipeline:
        _json(pipeline.reprocess_records(args.run_id, record_id=args.record_id))


@_register("serve")
def _run_serve(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    serve(config, args.host, args.port)


@_register("benchmark")
def _run_benchmark_cmd(args: argparse.Namespace) -> None:
    from ._main import _run_benchmark

    load_config(args.config)
    _run_benchmark(args)


@_register("convert")
def _run_convert(args: argparse.Namespace) -> None:
    """P3-2 任意格式互转：Reader × Writer = N×N 矩阵。"""
    # 第 2 批：经由 facade，组件化时只改一处。
    from ..services.document_facade import convert_with_preset, convertx_module

    # 保留模块引用而不是 `OutputRule = ...` 这种大写局部变量：
    # 后者会触发 N806（局部变量名混用类名风格），读者也会误以为它是导入的类。
    _convertx = convertx_module()
    convertx_convert = convert_with_preset

    preset = None
    if getattr(args, "preset_file", None) or getattr(args, "preset", None):
        if not getattr(args, "preset", None):
            print("convert: --preset-file 需要同时指定 --preset NAME", file=sys.stderr)
            raise SystemExit(2)
        from ..services.document_facade import convertx_preset

        try:
            preset = convertx_preset(args.preset, args.preset_file)
        except (KeyError, ValueError) as exc:
            print(f"convert: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        for warning in preset.warnings:
            print(f"convert: WARN {warning}", file=sys.stderr)

    preset_kwargs = dict(preset.convert_kwargs) if preset else {}
    table = str(getattr(args, "table", "records"))
    compression = str(getattr(args, "compression", "zstd"))
    on_error = str(getattr(args, "on_error", "skip"))
    if table == "records":
        table = str(preset_kwargs.get("table", table))
    if compression == "zstd":
        compression = str(preset_kwargs.get("compression", compression))
    if on_error == "skip":
        on_error = str(preset_kwargs.get("on_error", on_error))
    nested = bool(getattr(args, "nested", False) or preset_kwargs.get("nested", False))
    flat = bool(getattr(args, "flat", True) and not nested)

    rule_kwargs: dict[str, Any] = dict(preset.output_rule) if preset else {}
    if getattr(args, "output_dir", None):
        rule_kwargs["directory"] = args.output_dir
    if getattr(args, "on_conflict", None):
        rule_kwargs["on_conflict"] = args.on_conflict
    if getattr(args, "name_template", None):
        rule_kwargs["template"] = args.name_template
    if getattr(args, "preserve_timestamps", False):
        rule_kwargs["preserve_timestamps"] = True
    output_rule = _convertx.OutputRule(**rule_kwargs) if rule_kwargs else None

    options: dict[str, Any] = dict(preset.options) if preset else {}
    options.update({
        "reader_jsonl": {"flat": flat},
        "writer_jsonl": {"nested": nested},
        "reader_duckdb": {"table": table},
        "writer_duckdb": {"table": table},
        "writer_parquet": {"compression": compression},
    })
    result = convertx_convert(
        args.src,
        args.dst,
        src_format=getattr(args, "src_format", None) or None,
        dst_format=getattr(args, "dst_format", None) or None,
        options=options,
        table=table,
        compression=compression,
        on_error=on_error,
        output_rule=output_rule,
    )
    if not getattr(args, "quiet", False):
        written = result.extra.get("written_records")
        if result.status == "skipped":
            status = "⏭ 已跳过"
        elif result.status == "completed_with_errors":
            status = "⚠ 转换完成（有跳过记录）"
        elif result.warnings:
            status = "⚠ 转换完成（有异常）"
        else:
            status = "✅ 转换完成"
        # ★ 人类可读摘要一律走 stderr：stdout 必须保持**纯 JSON**，
        #   否则 `omnicrawler convert ... | jq` 这类管道会被这段文本打断（P2-2 唯一真 bug）。
        print(
            f"{status}: {result.source_format} → {result.target_format} "
            f"写入 {written if written is not None else '未知'} 行, {len(result.columns)} 列 -> {result.output_path}",
            file=sys.stderr,
        )
        for w in result.warnings:
            print(f"  ⚠ {w}", file=sys.stderr)
    _json({
        "ok": result.status != "skipped",
        "status": str(result.status),
        "source_format": result.source_format,
        "target_format": result.target_format,
        "rows": result.rows,
        "written_records": result.extra.get("written_records"),
        "columns": result.columns,
        "warnings": result.warnings,
        "issues": [
            {"code": issue.code, "message": issue.message, "severity": issue.severity}
            for issue in result.issues
        ],
        "output": str(result.output_path) if result.output_path else None,
        "extra": result.extra,
    })



@_register("notifications")
def _run_notifications(args: argparse.Namespace) -> None:
    from ..services.record_notifications import dispatch, report
    config = load_config(args.config)
    if args.action == "retry":
        from ..core.safe_action import require_explicit_apply
        require_explicit_apply("notifications retry")
        _json(dispatch(config, force=True, event_ids=set(args.event_id)))
    else:
        _json(report(config))
