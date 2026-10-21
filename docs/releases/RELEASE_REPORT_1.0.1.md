# OmniCrawler 1.0.1 发布报告

> 发布日期：2026-10-21；配置协议：v5；公共 API：兼容（纯扩展）；任务模型/工作区/导出格式：不变。

## 发布结论

1.0.1 是转换子系统（ConvertX）的架构补强版本：只吸收公开架构思想与工程模式，
不复制第三方源码，也未引入任何外部二进制。核心变化集中在**输出可控性、能力可见性、
文档出口、路由与批处理**，不改变既有任务执行、配置协议和 CLI 语义。
同时修复了 `rebase_venv` 在版本真源迁移后环境对账恒被跳过的问题。

## 已交付改进

### 一、输出规则与状态模型

- 新增 `output_rules.py`：命名模板（`{name}{ext}{date}{time}{index}`）、
  `overwrite/rename/skip/error` 四种冲突策略、时间戳保留、非法字符清洗。
- **源文件在任何策略下都不会被覆盖**；同路径冲突按策略显式处理，不再依赖写入器隐式行为。
- `ConvertResult` 新增 `status` 与 `issues`：区分完成、有警告、部分成功（有记录被跳过）
  与跳过；部分成功不再退化成布尔成功。
- CLI `convert` 与 `python -m omnicrawler.convertx` 增加
  `--output-dir/--on-conflict/--name-template/--preserve-timestamps`。

### 二、能力目录与可用性预览

- 新增 `catalog.py`：按族汇总可读/可写能力、缺失模块、安装 extra 与原因。
- `--list-paths --assume-installed` 可预览「装上某 extra 后新增哪些格式对」，
  `--capabilities-json` 输出机器可消费的能力快照。
- 图片族与表格/文档族保持隔离：跨族转换在规划与实际执行两层都会被拒绝。

### 三、文档出口补全

- 新增 `document_ir/writers.py`：HTML/DOCX/PDF/XLSX 出口，保留标题层级与表格。
- 新增 `.pdf` 读取；文档族形成 txt/md/html/docx/pdf/xlsx 的可写方向能力。
- 修复 Markdown 表格被空行截断的问题；转换过程通过共享缓存保留 DocumentIR，
  避免先把结构化内容压平成纯文本。

### 四、格式边图与计划

- 新增 `planner.py`：格式级边图 + Dijkstra（默认最多 3 步）+ image guard。
- `--plan` 只输出计划、不写文件；由于 records/IR 已提供直连，默认路由不会为多跳而多跳。
- 多跳执行保留给受限边与后续插件扩展；`execute_plan` 已覆盖临时目录与多步链路。

### 五、目录监听

- 新增 `watch.py`：静置确认、独占打开探测、临时后缀/隐藏文件过滤、
  `(size, mtime_ns)` 去重、全量重扫、输出目录排除。
- 支持 `--watch-once` 单轮扫描与常驻监听；单文件失败不终止监听循环。

### 六、配方与并发

- 新增 `presets.py`：内置 `table-archive/table-share/document-web/image-web`；
  未知键与未知枚举降级并告警，旧配置不会直接失败。
- 新增 `concurrency.py`：`EngineGate` 按图片/PDF/文档/列式表格分域限流，
  `convert_many` 保持输入顺序并隔离单任务失败。

### 七、GUI 与兼容修复

- GUI 自动建议路径改用统一 rename 语义；显式目标的既有覆盖行为保持不变。
- 目标格式不可写时禁用运行并给出原因提示；支持 txt/md/html/docx/pdf 与图片格式。
- 修复显式格式 `csv`/`jsonl`（无前导点，GUI 目标格式键形态）被拒绝的问题。

### 八、工具链修复

- `rebase_venv` 改为按序探测 `_version.py` → `__init__.py`，环境版本对账恢复生效；
  补回归测试：注入陈旧 editable 元数据 0.12.0 后可自愈回源码版本。

## 已知限制（如实声明）

- 多跳路由默认只用于受限边/插件扩展；直连可覆盖的格式对不会刻意多跳。
- HTML/DOCX/PDF/XLSX 等格式仍依赖对应可选依赖（openpyxl、python-docx、python-pptx、reportlab）；
  缺失时能力目录会给出安装提示，不会静默失败。
- GUI 侧通过离屏生命周期测试验证，未替代人工交互走查。
- 监听模式为轮询实现，超大目录应结合 `max_files` 与排除目录使用。

## 升级与兼容

- 从 1.0.0 升级为直接替换，无需配置迁移；配置协议仍为 v5。
- 任务模型、工作区格式、证据与导出格式不变；既有 CLI 子命令语义不变。
- 未指定输出规则时保持旧行为；自动建议路径使用 rename，显式目标沿用既有覆盖语义。
- 回滚：停止 GUI/Worker 后保留原工作区与配置，使用 1.0.0 完整同版本包回滚，
  不要混用不同版本的 EXE、`_internal` 或运行时资产。

## 验证证据

- 相关测试：ConvertX、DocumentIR、CLI、GUI 生命周期共 **400 passed**。
- 静态门禁：`python tools/self_verify.py --set static` **17/17 通过**。
- 架构预算：39 模块 / 61 边，未超预算。
- 三处关键护栏做了反向断言（注入缺口 → 测试变红）并逐字节还原：
  源文件覆盖保护、注册表门槛、图片跨族拒绝。
- 端到端冒烟：`--capabilities-json`、`--plan`、`--output-dir` + `skip`、
  `--watch-once`、`--preset table-share` 均通过。
