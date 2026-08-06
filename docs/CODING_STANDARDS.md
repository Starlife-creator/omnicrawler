# 编码规范（CODING_STANDARDS）

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

## 9. 子系统间整改样板迁移清单（S4.4）

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