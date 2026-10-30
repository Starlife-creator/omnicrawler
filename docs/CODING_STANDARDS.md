# 编码规范（CODING_STANDARDS）

> 适用版本：1.0.1 · 配置协议：v5 · 维护状态：现行（历史状态见归档；功能验证以验收账本为准）

> 本规范是各阶段优化必须遵循的全局纪律，源于审计根因 1（浅拷贝/可变默认值）。
> CI 层由 `tools/check_coding_standards.py` 强制执行；本地可运行 `python tools/check_coding_standards.py src tools cli`（默认扫 `src`）。

## 1. 配置合并一律深拷贝

- 任何 `dict(base)` / `obj.copy()` 起手的配置合并，改为 `copy.deepcopy`。
- 禁止把“模块级 DEFAULTS/可变默认值”就地改写；需要运行时覆盖时，先深拷贝再改副本。

## 2. `or` 仅用于布尔逻辑

- `x = a or default` 只在 `a` 的语义是“布尔真”时允许。
- 数值/计数场景必须用 `x = a if a is not None else default`（避免 `max_pages=0`、`seed=0`、`0.0` 被吞）。
- `catch` 里对 `None` 判空用 `is None`，不用 `not x`（避免 `0`/`""` 误判）。

## 3. 异常隔离

- 任何把“流量数据/用户输入”与“流程控制”混在同一 try 的地方，把构造/解析移入 try。
- 单条坏数据不得拖垮整批（逐条 try + 计入 failures）。
- 新增 `core/safe_data.py` 提供的 `safe_json_loads/safe_int/safe_float/safe_get/safe_slice` 优先于裸 `json.loads`/`int()`/`float()`。

## 4. 消费方存在性

- 新增任何“门禁 / 配置项 / 解析器”必须提供真实消费方（调用点），并配套“消费方存在性”单测。
- 零消费 = 孤儿代码，视为失败。

## 5. 破坏性操作防护

- 所有删除/重置/覆盖操作先经 `core/safe_action.py`（`require_explicit_apply` + `move_to_recycle`）。
- 未带 `--apply / --yes` 时只输出动作清单（dry-run），不删除任何数据。

## 6. 明文凭据禁止落盘

- 日志/快照/导出/模板/配置包中禁止出现明文 token/key/password/authorization。
- 敏感字段统一经 `core/secrets_store.py`（AES-GCM + keyring）或脱敏后输出。

## 7. CI 门禁一致性

- 本仓库已有 `check_release_integrity` / `check_architecture` / `check_docs_consistency` / `check_network_boundaries`。
- 新增门禁规则一律加入 `tools/check_coding_standards.py` 并在 `.github/workflows/quality.yml` 引用。

## 8. 正则与解析防护

- 用户可控正则必须经过 `safe_regex`（pdfx 同款）编译，避免灾难性回溯。
- 字段名进 XPath/CSS 时必须参数化或捕获解析异常。

## 9. SQL 片段一律白名单 + 参数化

- 任何排序键（`ORDER BY`）、列名、表名等 SQL 标识符，禁止用 f-string / 字符串拼接引入外部或配置输入，只能取自模块级白名单常量。
- 所有值一律用参数占位符（`?` / `:name`）绑定，禁止把运行时字符串插进 SQL 文本。
- 范例：`state/state_store.py::_CLAIM_ORDER`（策略名 → 固定 `ORDER BY` 从句），`claim()` 先经白名单取序、再以参数化 `LIMIT` 执行。
- 新增「可配置排序 / 列名 / 表名」时，必须先加白名单映射再使用；涉及 SQL 的 PR 评审强制检查本条。

## 10. 子系统间整改样板迁移清单（S4.4）

> 以下"样板"曾在单一子系统实现，现规定唯一消费路径；新代码禁止重复实现，旧调用点逐步迁移。

| 样板 | 唯一实现 | 已迁移调用点 | 禁止新增位置 |
|---|---|---|---|
| Excel/CSV 单元格安全化（公式注入防护 + 截断） | `core/utils.excel_safe(value, max_length=32700)` | pipeline/exporters.py、pdfx/exporter.py `safe_cell`（委托） | 任何自定义 `=`/`+`/`@` 前缀处理 |
| 归档解包安全 | `core/archive_security.py` | component_manager、updater | fetching/archives.py（已标 deprecated） |
| JSON 安全解析 | `core/safe_data.safe_json_loads` | pipeline、extraction、quality、pdfx | 裸 `json.loads` 处理外部数据 |
| 正则安全编译/执行 | `core/safe_data.safe_regex_search`（通用）与 `pdfx/safe_regex.py`（pdfx 域） | extractors、pdfx validation.value_pattern | 直接 `re.search` 用户正则 |
| 内容哈希 | `core/utils`/`pdfx/utils.sha256_file`（流式分块） | pdfx ingest（含 D45 命中补 SHA 校验） | `read_bytes()` 整读大文件算哈希 |
| 破坏性操作 | `core/safe_action.py` | cli workspace/recovery/components handler | 无 `--apply` 的删除/覆盖 |
| 种子密封/凭据 | `core/secrets_store.py` + `secret://` 引用 | gui config_serializer、credentials | 明文落盘 |

> 迁移新样板时：① 先在唯一实现处补单测；② 新调用点只 import 唯一实现；③ 旧实现标记 deprecated 并在下个大版本移除。

## 11. 行尾（换行）规则

换行规则以仓库根 `.gitattributes` 为唯一准据，文档与注释不得另起一套表述：

- 文本文件默认 LF（`* text=auto` 兜底）；
- 仅 `*.bat` / `*.cmd` 检出转 CRLF（cmd.exe 对 LF 兼容性差）；
- `*.ps1` / `*.sh` / `*.command` 与 `packaging/linux/**` 启动器钉死 LF，禁止在 Windows 检出后变成 CRLF；
- 签名载荷（`examples/plugins/**/*.py`、`plugins_installed/**/*.py` 及其 `.sig`）标记 `-text`，禁止改写字节。

写脚本注入式锚点（heredoc、字节比对、正则匹配行首行尾）前，先按字节确认目标文件的实际行尾；不要凭「本仓都是 CRLF / LF」的旧印象假设。

## 12. 覆盖率分组门禁与排除记录

覆盖率分组门禁的唯一判据在 `tools/check_coverage_gates.py`：既有阻断分组按 CI 三平台实测最小值 − 2 设定下限，只升不降。

C-#8 风险优先补充分组 `services`、`plugins`、`commands`、`gui_views` 当前处于 **baseline pending** 状态：门禁会打印实测值与占位下限，但**不阻断合并**；首轮 CI 覆盖率数据落地后，按同一口径（min − 2）填回实数下限并从 `BASELINE_PENDING_GATES` 移出，即转为阻断。

`src/omnicrawler/visual_selector/` 在 `pyproject.toml` 的 coverage `omit` 中排除：它是浏览器坐标选择 E2E 工具，依赖真实页面与浏览器运行时，不进单测覆盖率。**复核期限：2026-12 前**为其补 offscreen/集成测试，达标后从 omit 移除并纳入分组门禁；在此之前不得以「覆盖率低」为由删除该模块功能。

## 13. GUI 测试归属：`tests/unit/gui` 与 `tests/gui`

两个目录的职责边界成文如下，新增 GUI 测试前先按本表选目录：

| 维度 | `tests/unit/gui` | `tests/gui` |
| --- | --- | --- |
| 定位 | 纯逻辑、组件单元、信号/参数契约 | 窗口/对话框生命周期、集成、跨组件契约、offscreen 端到端冒烟 |
| QApplication | 不依赖完整生命周期；Qt 不可用时 `pytest.importorskip("PySide6")` 跳过 | 集中使用 `tests/gui/conftest.py` 的 offscreen 环境 |
| 夹具 | 不引用 `tests/gui` 的 fixture/helper | 可复用 `tests/unit/gui` 已沉淀的 helper，反向禁止 |
| 覆盖率归属 | `desktop_core` 等单测分组 | 集成/生命周期覆盖，不进单测分组下限 |

阻断规则由 `tools/check_gui_test_layout.py` 执行（接入 `quality.yml` 静态 job）：

1. 两目录不得出现同名测试文件；
2. `tests/unit/gui` 不得 import `tests/gui`（单元层禁止依赖集成层）；
3. `tests/gui` 不得 import `tests/unit/gui`（避免单元层被集成夹具绑架）。

**重复覆盖候选清单（advisory，非阻断）**：`tools/check_gui_test_layout.py --report` 实时列出「同一 `omnicrawler.gui` 源模块被两个目录同时引用」的测试文件对。2026-10 盘点共 26 个模块处于双目录覆盖状态，集中在 `core.config_model`、`design_system`、`views.plugin_market`、`views.change_monitor`、`delegates.*` 等簇；去重原则：纯函数/信号契约留在 `tests/unit/gui`，生命周期与跨组件装配断言并入 `tests/gui`，合并后删除重复用例。物理去重需在可运行 Qt offscreen 的 CI 环境逐簇回归验证，本轮先落归属规则与候选清单，去重迁移列入后续迭代。
