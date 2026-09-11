# 生产运行、恢复与质量验收

> 适用版本：0.12.0 · 配置协议：v5 · 维护状态：现行

## 上线顺序

1. `omnicrawler validate -c <config>`：配置契约。
2. `omnicrawler doctor -c <config>`：依赖、磁盘、浏览器和安全设置。
3. `omnicrawler run -c <config> --max-pages 20`：小样本试跑。
4. 核对 robots/条款、范围、字段证据、附件和质量统计。
5. 逐步提高页数和并发，不在首次运行直接放大。

建议生产配置：

```yaml
incremental: {archive_raw: true, skip_unchanged: true}
resources:
  minimum_free_disk_bytes: 2147483648
  maximum_runtime_seconds: 21600
  maximum_workspace_bytes: 53687091200
storage:
  records: {backends: [], fail_open: true}
```

## 恢复与重处理

```powershell
# 中断、断电或 worker 异常后恢复 frontier
omnicrawler resume -c configs/project.yaml

# 把失败且可重试的 URL 重新投入任务
omnicrawler run -c configs/project.yaml --retry-failed

# 字段规则或 transformer 改变后，不访问网络重做派生阶段
omnicrawler reprocess -c configs/project.yaml --run-id <run_id>
```

`reprocess` 在删除派生结果前验证每个既有记录都有位于工作区内的原始归档；缺失、软链接或越界路径会拒绝执行。开始和结束均写入 `audit_events`。

PDF 子项目支持阶段重置：

```powershell
pdfx --config work/project/pdf/project.yaml reset parse
pdfx --config work/project/pdf/project.yaml reset ocr
pdfx --config work/project/pdf/project.yaml reset extract
```

## 状态和诊断

```powershell
omnicrawler status -c configs/project.yaml
omnicrawler serve -c configs/project.yaml --host 127.0.0.1 --port 8765
```

- `state.sqlite3`：runs、frontier、responses、records、quality、errors、audit。
- `diagnostics/<run_id>/`：脱敏失败上下文。
- `output/pipeline_summary.json`：任务状态、计数、插件、导出与存储警告。
- `output/metrics.json`、`metrics.prom`：指标快照与 Prometheus 文本。

### 终态语义：异常不得伪装成功

一轮任务的终态只有四种，由 `omnicrawler/core/run_state.py` 统一定义：

| 终态 | 含义 | 产生条件 |
|---|---|---|
| `succeeded` | 正常完成 | 没有错误记录 |
| `partial_success` | 完成但有错误记录（别名 `completed_with_errors`） | 有页面交付，同时 `errors` 非空 |
| `failed` | 什么都没交付 | 有请求被尝试，但一页都没成功 |
| `cancelled` | 被取消 | 暂停/停止或收到中断 |

判定发生在爬取阶段结束、导出阶段之前，依据是流水线**自己写入的 `errors` 记录**
（与 GUI 文案「任务部分成功(存在错误记录)」同源）。三条容易踩错的边界：

- **逐请求失败是隔离处理的**——一个坏 URL 不该毁掉整轮；但隔离之后必须聚合到终态，
  否则「全部抓取失败」也会报成 `succeeded`，即**异常伪装成功**。
- **空 frontier 不是失败**：增量重跑时无事可做（没有任何请求被尝试），终态仍是 `succeeded`。
  判据区分「没做事」与「做事全失败」，两者不可混为一谈。
- **`failed` 且 `errors` 为空时会补写一条阶段级错误**（例如失败发生在请求发出之前），
  避免出现「任务失败但查不到为什么」。

终态直接决定 CLI 退出码：`failed` / `cancelled` 一律为 1；严格模式（`--strict`）下
只有 `succeeded` 且有效记录 > 0 才是 0，因此 `partial_success` 在严格模式下也计为 1。

### 定位一次失败

1. `runs.status` 与 `output/pipeline_summary.json`：本轮终态与错误计数；
2. `frontier.status`：哪些 URL 落到 `failed`（重试次数耗尽）或 `blocked`（策略拦截），
   各自带 `last_error`；
3. `errors` 表：每次失败的 `run_id` / `url` / `stage` / `error_type` / `message`；
4. `diagnostics/<run_id>/`：脱敏后的失败上下文。

恢复：`omnicrawler run -c <配置> --retry-failed` 会把 `frontier` 中 `failed` 的记录
重新投入 `pending`，且**不重置已完成的工作**。
核心指标包括请求数/错误数/时延、浏览器升级、frontier pending、记录数、字段完整率/校验通过率、磁盘余量、PDF 文档和 OCR/处理计数。标签限制为主机、阶段、引擎和错误类别，避免 URL 级高基数。

## 资源保护

ResourceGuard 周期检查磁盘余量、工作区大小和运行时长。触发时状态为 `resource_limited`，已完成数据不回滚，错误和指标照常写入。响应、解压、网络捕获、robots 和 PDF 均另有局部大小边界。

## 调度

```powershell
omnicrawler schedule add -c configs/project.yaml --name hourly --every-seconds 3600
omnicrawler schedule list
omnicrawler schedule run-due --limit 10
```

GUI 专业模式可添加、启用和停用相同任务。SQLite 调度库用租约防止多个触发进程重复领取；Windows 任务计划程序或 cron 只需周期执行 `run-due`。

## 外部存储

- 对象后端 `s3`/`mirror` 同步保留本地副本，确保桌面预览和 PDF 能继续工作。
- PostgreSQL/OpenSearch 是结果镜像；默认 `fail_open: true` 时失败进入 `storage_warnings`，不会破坏本地恢复。
- 严格交付任务可设置 `fail_open: false`，但必须预先演练外部服务中断。

## 人工复核

1. 打开 `review_queue.csv` 或 `extraction_results.xlsx`。
2. 核对原始值、清洗值、来源 URL、选择器/路径、页码和置信度。
3. 修改复核决定与修正值。
4. PDF 流程使用 `pdf-extract ... apply-review` 回写，然后重新导出。

人工修改写审计信息，Excel/CSV 输出会防止公式注入。

## 保留与清理

先预览，再显式执行：

```powershell
omnicrawler cleanup -c configs/project.yaml
omnicrawler cleanup -c configs/project.yaml --apply
```

在确认重处理窗口结束前不要删除 raw；交付文件、审计库和 SBOM 应按组织保留政策独立备份。

## 发布验收

- 全量测试、coverage、ruff、mypy、compileall 全绿。
- `templates validate --include-legacy` 通过。
- GUI offscreen 与 Chromium 动态页面测试通过。
- wheel 可安装，模板资源在 wheel 内。
- SBOM 生成且 JSON 可解析。
- Docker 构建在可用环境通过。
- 小样本真实站点运行需由数据所有者确认条款和字段结果。
