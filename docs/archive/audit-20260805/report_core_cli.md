# 审查报告: core/cli/commands/apps

- 审查方式：逐行通读全部 36 个目标文件（共 4134 行），并对跨模块引用做符号级核对
- 语法检查：`python -m py_compile` 全部 36 个文件 → **全部通过，无编译失败**
- 环境探测：`import omnicrawl` 实测加载 273 个 omnicrawl.* 子模块，用时约 0.28–0.94s
- 范围：src/omnicrawl/{__init__,__main__,core,cli,commands,apps}（不含 pipeline/gui/pdfx 主体）

## 汇总（按严重级别计数）

| 严重级别 | 数量 |
|---|---|
| critical | 0 |
| high | 4 |
| medium | 6 |
| low | 14 |
| ux | 3 |
| **合计** | **27** |

---

## 问题清单

### [high] src/omnicrawl/__init__.py:147-159,192 - 包导入时急切导入全部 116 个兼容模块（重启动成本 + 副作用）

- 现状: `_setup_compat_aliases()` 在包加载末尾无条件执行，对 `_DEPRECATED_MODULE_MAP` 中全部 116 项逐一 `importlib.import_module(...)`；实测 `import omnicrawl` 经传递导入共加载 273 个 `omnicrawl.*` 子模块（services×28、runtime×13、fetching×12、extraction×8、plugins×8、templates×8 等），耗时 0.28–0.94s。
- 问题: 每个 CLI 命令启动都要 `from .. import __version__`（cli/_main.py:12），即每个 `omnicrawl xxx` 都要承受整包导入开销；任何被映射模块的顶层导入副作用都会在“仅 import 包”时执行；`except Exception: logger.debug(...)`（159 行）把所有失败静默吞掉（debug 级别默认不可见），掩盖真实导入错误。
- 建议: 删除 192 行的急切调用，仅保留 `__getattr__`（162-188 行）的按需重定向路径；或改为按需注册：第一次 `from omnicrawl.<old> import ...` 时再导入。

### [high] src/omnicrawl/core/ai_env.py:84-96 - load_ai_env 文件优先级颠倒（用户级 .env 反而覆盖项目级）

- 现状: `ai_env_candidates()`（50-61 行）按 [项目 .env, 当前目录 .env, 用户级 ~/.omnicrawl/.env] 返回（高→低）；`load_ai_env` 用 `for path in ai_env_candidates(...): merged.update(parse_env_file(path))`（91-92 行）顺序合并。
- 问题: 后合并者覆盖先合并者，即**最低优先级**的用户级 .env 最后写入，覆盖了项目/当前目录值，与 docstring“os.environ > 项目 > cwd > 用户级”完全相反。若项目 .env 设了 OMNICRAWL_AI_API_KEY，而用户级 .env 残留旧值，会静默用错配置。
- 建议: 按优先级从低到高遍历，即 `for path in reversed(ai_env_candidates(project_root)):`，或改为 `for path in ai_env_candidates(...)[::-1]` 后仍用 os.environ 覆盖（93-95 行保持）。

### [high] src/omnicrawl/core/utils.py:35-42 - deep_merge 对未覆盖的嵌套 dict 与原 DEFAULT 别名共享，会被运行时写入污染全局默认值

- 现状: `deep_merge` 只 `dict(base)` 浅拷贝顶层，未在 override 中出现的嵌套段（如 `crawl`）直接复用 `DEFAULTS["crawl"]` 同一对象。而 `ApplicationService.run`（services/application_service.py:89）会执行 `config.raw["crawl"]["max_pages"] = max_pages`，由 `omnicrawl run --max-pages N`（commands/run_task.py:62-67）触发。
- 问题: 当用户配置没有 `crawl:` 段时，`merged["crawl"] is DEFAULTS["crawl"]`，该赋值永久改写模块级 `DEFAULTS`。同一进程后续 `load_config` 其他配置（GUI/服务端/测试）会继承错误的 max_pages 默认值；配置对象间的状态互相污染。
- 建议: `deep_merge` 内用 `copy.deepcopy(base)` 起始，或 `load_config` 对 merged 结果做一次 deepcopy；并避免在运行时直接改 `config.raw`（改用副本后写回）。

### [high] src/omnicrawl/core/config.py:219-221 与 src/omnicrawl/cli/_handlers.py:405-409 - `omnicrawl validate` 的失败分支是死代码，JSON 契约失效

- 现状: `load_config` 在 `validate_config` 有 errors 时直接 `raise ValueError`；`_run_validate` 先 `load_config(args.config)` 再 `validate_config(config)` 并输出 `{"ok": not errors, ...}`。
- 问题: 配置无效时 load_config 已抛异常，`_run_validate` 永远不会执行到输出 `{"ok": False, "errors": [...]}` 的分支——用户只会看到 stderr 上的“错误: ValueError: ...”文本；依赖 `{"ok":false}` 的脚本无法解析输出（死代码 + 行为不一致）。
- 建议: 为 validate 提供“只校验不抛”路径，如给 `load_config` 加 `strict=False` 参数（出错时返回 AppConfig 并携带 errors），validate 命令据此输出结构化 JSON；退出码仍为 1。

---

### [medium] src/omnicrawl/commands/template.py:97-105 - render 后的“渲染结果校验”是死代码，--force 放行分支永不触发

- 现状: 写盘后调用 `load_config(target_path)`（98 行），随后 `validate_config(loaded)` 并 `if errors:` 打印明细/抛错。
- 问题: `load_config` 内部已对无效配置抛 `ValueError`，程序根本到不了 99-105 行；`--force` 下“已放行，请手工修复”的 stderr 提示（103 行）永不输出，用户会拿到一条缺少字段明细的原始异常。
- 建议: 校验改用非抛出版本（同 high #4 的 `strict=False`），把 errors 明细放进提示；或在 render 前先用纯 `yaml.safe_load`+`validate_config` 校验并捕获错误。

### [medium] src/omnicrawl/core/runtime_manifest.py:45 - verify_runtime_manifest 对畸形清单会抛未捕获 KeyError/TypeError

- 现状: 45 行 `int(expected["bytes"])` 与 `expected["sha256"]` 直接按 `dict` 取值，未做类型/键存在校验；`value = json.loads(...)` 与 `files = value.get("files", {})`（33-34 行）亦未捕获 JSON 解析异常。
- 问题: 校验逻辑本身是安全组件，面对被篡改/损坏的清单，应返回 `"ok": False, "status": "invalid"`，而不是让 CLI 抛 KeyError 回溯崩掉；`runtime-verify` 命令（cli/_handlers.py:164-170）因此可能无法给出任何诊断。
- 建议: 逐条 try/except，条目不是 dict 或缺少 bytes/sha256 时记入 corrupt 列表并 continue；`json.loads` 包 try 返回 missing_manifest/invalid。

### [medium] src/omnicrawl/core/utils.py:45-63 - canonicalize_url 访问 parts.port 在 try 之外，畸形端口抛出未捕获 ValueError

- 现状: 56 行 `port = parts.port` 位于 47-49 行 `try` 之外，仅 urljoin/urlsplit 被保护。
- 问题: 对 `http://host:99999/` 这类 URL，`urlsplit` 不报错而 `parts.port` 抛 `ValueError: Port out of range 0-65535`，函数本应返回 None（信号“URL 不可规范化”）却向上抛异常，调用方（如去重集合）可能中断。
- 建议: 把 `parts.port`（乃至 hostname 访问）一并放进 try，异常时 `return None`。

### [medium] src/omnicrawl/core/archive_security.py:88-106 - copy_zip_member 出错留半写文件；已存在目标抛裸 FileExistsError

- 现状: 目标以 `"xb"` 打开（97 行），写入过程中校验 `written > info.file_size` 抛错时（100-101 行）目标文件已部分写出且不清理；目标已存在时（升级/重装场景）抛原始 `FileExistsError` 而非 `UnsafePackageError`。
- 问题: 半写文件留在磁盘上会破坏“校验和验证”的可信度（下轮校验会报 corrupt 但无法区分来源）；异常类型不统一使上层无法用单一异常处理安全错误。
- 建议: 抛错前 `target.unlink(missing_ok=True)`；对已存在文件统一转为 `UnsafePackageError`（或提供 `overwrite` 语义并由调用方决定）。

### [medium] src/omnicrawl/commands/run_status.py:47-49 与 src/omnicrawl/cli/_handlers.py:433-435 - `status --format text` 同时输出人类文本和完整 JSON

- 现状: `execute(..., output_format="text")` 内部调用 `_print_text(result)` 打印到 stdout 并返回 dict；`_handlers._run_status` 随后无条件 `_json(cmd_status.execute(...))`。
- 问题: `omnicrawl status --format text` 会先打印一段带 emoji 的可读文本，紧接着再打印整段 JSON——输出被污染，脚本与人都难受；`--format json` 时则正常。
- 建议: 让 handler 根据 `args.format` 决定只 `_json` 或只 `_print_text`（把打印职责收敛到 handler 一处），或让 `_print_text` 返回字符串供 handler 统一处理。

### [medium] src/omnicrawl/core/errors.py:59-82 - 定义了一整套类型化异常却从未被抛出

- 现状: `ConfigParseError`/`TemplateValidationError`/`BrowserEngineError`/`SelectorSyntaxError` 等类已定义（59-82 行），全库检索仅在 quality/diagnostics.py 的正则字符串中出现过 `SelectorSyntaxError`；`load_config`（config.py:197-221）、模板渲染、PDF 入口均抛裸 `ValueError`/`FileNotFoundError`。
- 问题: `describe_error`（errors.py:84-91）的分支与 `_print_error_hint`（cli/_main.py:305-325）的“模板不存在/配置语法错误”提示永远不会命中，用户得到的建议是泛化的“运行 doctor”；死代码掩盖了错误语义。
- 建议: 在 `load_config` 的 yaml 解析处抛 `ConfigParseError`（from exc）、模板校验处抛 `TemplateValidationError`、浏览器缺失处抛 `BrowserEngineError`，让错误边界真正可用。

---

### [low] src/omnicrawl/core/credentials.py:11 - 环境变量前缀 OMNICRAW_SECRET_ 少了个 L，与全项目命名不一致

- 现状: `env_name = "OMNICRAW_SECRET_" + ...`，全库仅此一处使用该前缀。
- 问题: 项目其余环境变量均为 `OMNICRAWL_*`，用户按约定设 `OMNICRAWL_SECRET_X` 将永远读不到（静默走 keyring 或抛错）；疑似拼写错误。
- 建议: 改为 `OMNICRAWL_SECRET_`，并对旧前缀做兼容读取。

### [low] src/omnicrawl/core/ai_env.py:69,123 - .env 固定按 UTF-8 读取，GBK 编码文件抛未捕获 UnicodeDecodeError

- 现状: `parse_env_file` 与 `save_ai_env` 均 `path.read_text(encoding="utf-8")`。
- 问题: 中文 Windows 上用户手写的 GBK/cp936 .env（含中文注释）会在读取时直接抛 `UnicodeDecodeError`，无降级提示；跨平台健壮性不足。
- 建议: 读取时 `encoding="utf-8", errors="replace"` 或捕获后回退到 `locale.getpreferredencoding()` 并打 warning。

### [low] src/omnicrawl/commands/schedule.py:13-15 - `_json` 辅助函数定义后从未使用

- 现状: 模块内 `_json` 定义存在，`execute` 全部路径返回 dict，由 `cli/_handlers._run_schedule` 负责序列化。
- 问题: 死代码。
- 建议: 删除。

### [low] src/omnicrawl/cli/_main.py:520-530 与 src/omnicrawl/commands/template.py:18-28 - `_key_values` 两处逐字重复

- 现状: 两个模块各自定义了一份完全相同的 `_key_values`（在 _main 中仅作为 `cli/__init__` 再导出被使用）。
- 问题: 重复代码，后续修改易漏改一处。
- 建议: 收敛到 `core.utils` 单一定义并复用。

### [low] src/omnicrawl/cli/_main.py:215 与 src/omnicrawl/cli/_handlers.py:173-180 - `import-easyspider --ir` 选项解析了但从未使用

- 现状: parser 定义了 `--ir`（输出 Task IR JSON 的开关），handler 中只有 `import_easyspider(args.json, output_path=args.output)`。
- 问题: 用户传 `--ir` 无任何效果，静默失效（死选项）。
- 建议: 在 handler 中实现该分支或移除选项。

### [low] src/omnicrawl/core/logging_utils.py:34 - configure_logging 对非法 level 字符串抛裸 AttributeError

- 现状: `root.setLevel(getattr(logging, level.upper()))` 无校验。
- 问题: 该函数是公共工具，被非 argparse 调用方（如 GUI/测试）传入小写或非法级别时会抛晦涩的 AttributeError。
- 建议: 用 `logging._nameToLevel.get(level.upper(), logging.INFO)` 或显式校验并 raise ValueError。

### [low] src/omnicrawl/commands/field.py:12 - execute_field_suggest 默认 limit=20 与 CLI 默认 limit=100 不一致

- 现状: 函数默认 `limit=20`，`_main.py:132` 的 `--limit` 默认 100，handler 显式传参掩盖了差异。
- 问题: 直接以库方式调用该函数时行为与 CLI 不同，属默认值不一致。
- 建议: 统一为 100 或对 CLI 显式传参并加注释。

### [low] src/omnicrawl/commands/init_project.py:30-35 - 项目名直接拼进输出文件名，未净化（可写出目标目录之外）

- 现状: `target = target_dir / f"{name}.yaml"`，`name` 是 CLI 位置参数，未做任何净化；随后 `data.setdefault("project", {})` 假定模板解析结果是 dict。
- 问题: `omnicrawl init "../../evil"` 之类的名称会把文件写到 `target_dir` 之外（本地文件系统穿越）；空/非对象模板会抛 AttributeError。
- 建议: 对 name 复用 wizard 中的净化规则（`_NAME_RE`），并对 `yaml.safe_load` 结果做 `isinstance(data, dict)` 校验。

### [low] src/omnicrawl/core/ai_env.py:99-105 - _format_env_line 不处理含换行的值，会写坏 .env

- 现状: `_QUOTE_CHARS` 不含 `\n`，值含换行时直接拼接成多行。
- 问题: 生成的 .env 被破坏，后续解析错位。
- 建议: 遇到 `\n`/`\r` 时替换为字面 `\n` 序列或拒绝写入并报错。

### [low] src/omnicrawl/core/runtime_paths.py:211-218 - bundled_browser_executable 仅匹配 Windows 路径，冻结版在 Linux/macOS 永远找不到 Chromium

- 现状: 仅搜索 `chromium-*/chrome-win/*` 与 `chrome-win64/*` 两个模式。
- 问题: 与 `capabilities._playwright_browser`（core/capabilities.py:81）覆盖全部平台的模式不一致；非 Windows 冻结构建的浏览器发现能力缺失。
- 建议: 增加 `chrome-linux/chrome` 与 `chromium-*/chrome-mac/...` 模式（按 sys.platform 过滤）。

### [low] src/omnicrawl/cli/_main.py:257-259,328-347 - pdf 子命令不注册到 argparse，`--help` 不显示、参数顺序受限

- 现状: `pdf`/`pdf-process`/`pdf-extract` 靠 `argv[0] in {...}` 前缀匹配分发，未加入 `build_parser`。
- 问题: `omnicrawl --help` 看不到这些命令；`omnicrawl --log-level DEBUG pdf ...` 因 argv[0] 不是 pdf 而不会分发（参数被 argparse 拒绝）。
- 建议: 将这些命令注册为隐藏 subparser 并在 handler 中转发，或明确提示 “可用 omnicrawl pdf --help”。

### [low] src/omnicrawl/core/capabilities.py:199-201 - all_optional_ready 对未导入模块默认乐观判定为可用

- 现状: `all(item["installed"] and item.get("importable", True) for ...)`，quick/task 模式下未导入模块无 `importable` 键，`get` 默认 True。
- 问题: 即使某可选模块实际导入会失败，quick 模式仍可能报告 `all_optional_ready: true`，误导诊断。
- 建议: 未导入的模块不计入或按 `installed` 判“未验证”并在输出标注 unverified。

### [low] src/omnicrawl/commands/recovery.py:28 与 src/omnicrawl/commands/workspace.py:24 - --backup/--target 路径未做 expanduser/resolve 统一处理

- 现状: `center.rollback_config(Path(backup))`、`manager.rollback(Path(target))` 直接用原始字符串。
- 问题: 与其它命令（如 plan.py、template.py）先 `expanduser().resolve()` 的惯例不一致，`~` 路径在部分场景会落错位置。
- 建议: 统一 `Path(...).expanduser().resolve()`。

### [low] src/omnicrawl/commands/__init__.py:10-13 - `__all__` 列出的子模块名未在包内 import

- 现状: `__all__` 声明 run_task/run_status/... 等模块名，但 `__init__` 并未导入它们。
- 问题: `from omnicrawl.commands import run_task` 依赖 Python 的子模块自动导入机制才成立，`import *` 与静态检查工具可能误判；属性访问时序不一致。
- 建议: 在 `__init__` 显式 `from . import run_task, ...` 或移除 `__all__`。

### [ux] src/omnicrawl/cli/_main.py:503-517 - wizard 结束同时输出人类文本块与机器 JSON 到 stdout

- 现状: `_print_plan_summary(...)` 打印多行“下一步建议”后，`_json({...})` 再打印一串 JSON。
- 问题: 交互用户看到一行突兀的 JSON；脚本用户看到整段无关文本。输出协议混乱。
- 建议: 交互模式只打印人类文本；仅当 `--json` 等显式开关时输出 JSON。

### [ux] src/omnicrawl/cli/_main.py:476-481 - wizard 输出格式输入未校验，非法值静默写入配置

- 现状: `formats = [f.strip() ...]` 后直接 `"outputs": {fmt: True for fmt in formats}`，未过滤非法格式。
- 问题: 输入 `jsonl,yaml` 会在配置里生成 `outputs.yaml: true`，运行阶段被默默忽略，用户困惑。
- 建议: 仅接受 jsonl/csv/xlsx 并提示重输，或把未知项列 warning。

### [ux] src/omnicrawl/cli/_handlers.py:120-125 - templates validate 存在双重退出码判断

- 现状: 120-122 行对 `ok is False` 已 `raise SystemExit(1)`；124-125 行又对 validate 重复 `SystemExit(0 if ok else 1)`。
- 问题: 冗余逻辑，维护时易引入不一致（如 ok 非 bool 时行为漂移）。
- 建议: 合并为单一退出码判定。

---

## 附：跨模块调用核对结果（已确认一致，未列问题）

- `cli/_handlers.py` → `pipeline.build_registry`、`services.doctor.run_doctor(AppConfig, *, probe_ai)`、`services.server.serve(AppConfig, host, port)`、`state.StateStore`/`runtime.scheduler.ScheduleStore` 上下文管理器 均存在且签名匹配。
- `commands/template.py` → `sources.site_inspector.inspect_url(url, catalog, *, timeout_seconds)` 匹配。
- `apps/pdf_processor.py` → `pdfx.project.create_project_config(...)`；`apps/field_extractor.py` → `pdfx.service.run_extraction/run_processing` 关键字参数均匹配。
- `commands/field.py` → `fetching.action_recorder.record_with_playwright(url, output, *, timeout_seconds)` 匹配。

## 附：语法检查记录

对全部 36 个文件运行 `python -m py_compile`，无任何文件失败。空模块（core/__init__.py、apps/__init__.py、cli/__init__.py 3 行）按约定略过未报。
