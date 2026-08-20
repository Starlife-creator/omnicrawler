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


@_register("workbench")
def _run_workbench(args: argparse.Namespace) -> None:
    from ..services.workbench import main as workbench_main

    raise SystemExit(workbench_main())


@_register("field-suggest")
def _run_field_suggest(args: argparse.Namespace) -> None:
    _json(cmd_field.execute_field_suggest(
        args.html, output=args.output or "", limit=args.limit,
    ))


@_register("record-actions")
def _run_record_actions(args: argparse.Namespace) -> None:
    _json(cmd_field.execute_record_actions(args.url, args.output, timeout=args.timeout))


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
    if command == "audit":
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
        is_main()
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


@_register("components")
def _run_components(args: argparse.Namespace) -> None:
    from ..core.safe_action import require_explicit_apply

    if args.action in {"uninstall", "rollback"}:
        require_explicit_apply(f"components {args.action}")
    _json(cmd_components.execute(
        args.action, package=args.package or "", name=args.name or "",
        allow_unsigned=bool(args.allow_unsigned), sha256=args.sha256 or "",
    ))


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
    ))


@_register("transform")
def _run_transform(args: argparse.Namespace) -> None:
    _json(cmd_transform.execute(
        args.source,
        args.target,
        maps=args.map,
        transform_steps=args.transform_steps,
        src_format=args.src_format,
        dst_format=args.dst_format,
        dry_run=args.dry_run,
        confirm=args.confirm,
        batch_size=args.batch_size,
        max_records=args.max_records,
        on_error=args.on_error,
        preview_limit=args.preview_limit,
    ))


@_register("workspace")
def _run_workspace(args: argparse.Namespace) -> None:
    from ..core.safe_action import require_explicit_apply

    if args.action == "rollback":
        require_explicit_apply("workspace rollback")
    load_config(args.config)
    _json(cmd_workspace.execute(args.config, args.action, target=args.target or "", kind=args.kind))


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
    _json(cmd_recovery.execute(args.config, args.action, limit=args.limit, backup=args.backup or ""))


@_register("compare-runs")
def _run_compare_runs(args: argparse.Namespace) -> None:
    from ..review.run_compare import compare_runs

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
        _json(pipeline.reprocess_records(args.run_id))


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
    from ..convertx import convert as convertx_convert

    options: dict[str, Any] = {
        "reader_jsonl": {"flat": bool(getattr(args, "flat", True))},
        "writer_jsonl": {"nested": bool(getattr(args, "nested", False))},
        "reader_duckdb": {"table": str(getattr(args, "table", "records"))},
        "writer_duckdb": {"table": str(getattr(args, "table", "records"))},
        "writer_parquet": {"compression": str(getattr(args, "compression", "zstd"))},
    }
    result = convertx_convert(
        args.src,
        args.dst,
        src_format=getattr(args, "src_format", None) or None,
        dst_format=getattr(args, "dst_format", None) or None,
        options=options,
    )
    if not getattr(args, "quiet", False):
        print(
            f"✅ 转换完成: {result.source_format} → {result.target_format} "
            f"共 {result.rows} 行, {len(result.columns)} 列 -> {result.output_path}"
        )
        for w in result.warnings:
            print(f"  ⚠ {w}")
    _json({
        "ok": True,
        "source_format": result.source_format,
        "target_format": result.target_format,
        "rows": result.rows,
        "columns": result.columns,
        "warnings": result.warnings,
        "output": str(result.output_path) if result.output_path else None,
        "extra": result.extra,
    })

