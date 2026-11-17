# PDF 工作台迁移遗留清单

> 记录时间：2026-11-22 · 迁移完成后的**已知遗留与刻意保留**
>
> 这份文件回答一个问题：**为什么宿主里还能看到"pdf"字样？**

## 一、已彻底移出的（不可回退）

| 内容 | 位置（迁移前 → 现在） |
|---|---|
| `pdfx`（30 文件） | `src/omnicrawler/pdfx/` → 插件 `omnicrawler_pdf/pdfx/` |
| `convertx`（13 文件） | → 插件 `omnicrawler_pdf/convertx/` |
| `document_ir`（7 文件） | → 插件 `omnicrawler_pdf/document_ir/` |
| `apps`（4 文件） | → 插件 `omnicrawler_pdf/apps/` |
| `gui/views/pdf_*.py` 等 8 个视图 | → 插件 `omnicrawler_pdf/gui_views/` |

宿主侧用 `migrated_packages_absent()` 断言这些包已不存在，
`tests/unit/tools/test_migration_fidelity.py` 保证插件侧与归档原件逐行一致。

## 二、**刻意保留**在宿主的（有正当理由）

| 文件 | 为什么不能删 |
|---|---|
| `cli/_parsers/pdf.py` | `omnicrawler pdf` 子命令的**参数定义**。删了用户就没有入口（命令体的实现在插件里） |
| `doc_extractors/pdf.py` | 独立用 pdfplumber 做文本抽取，属支柱①的通用能力，与文档工作台无关 |
| `pipeline_ops/pdf_integration.py` | 采集主流程挂接 PDF 阶段的编排点，经 facade 调插件 |
| `pipeline_ops/pdf_stage.py` | 同上：阶段编排（读 `processors.pdf.enabled`、gauge、checkpoint） |
| `pipeline_ops/pdf_region.py` | 区域规则的数据结构（`make_region_rule`），插件与宿主都用到 |
| `services/pdf_quality_benchmark.py` | 质量基准评分，属支柱① |
| `core/builtin_references.py` | `builtin:pdf/` 引用解析，是**宿主的公开解析 API** |
| `templates/pdf/`（3 个文件） | 见下节 |

这些是**入口层**（参数定义、编排挂点、通用能力），不是 PDF 工作台的实现。
删掉它们等于砍掉用户入口或支柱①的功能。

## 三、已知的技术债：`templates/pdf/` 有两份

宿主 `src/omnicrawler/templates/pdf/` 与插件
`omnicrawler_pdf/templates/pdf/` 各有一份，**内容逐字节相同**
（`generic_template.yaml` / `announcement_fields.yaml` / `entity_master.csv`）。

### 为什么现在两份都在

- 宿主侧：`resolve_pdf_template()` → `resolve_builtin_pdf_reference()` 会读
  `templates/pdf/<name>`。这条链**在插件未安装时也会执行**
  （`pdf_integration.ensure_pdf_project` 先解析模板、再调 `pdf.create_project`）。
- 插件侧：`compat/builtin_references.py` 按同样的相对路径找
  `omnicrawler_pdf/templates/pdf/<name>`。插件必须**自包含**才不依赖宿主目录结构。

所以两份各有用途。

### 实测验证（两份都工作正常）

| 场景 | 结果 |
|---|---|
| 插件未装，调 `ensure_pdf_project` | `RuntimeError: PDF 工作台插件未安装…请在「插件市场」中安装 pdf-workbench` ✅ 可操作 |
| 插件已装，调 `ensure_pdf_project` | 成功创建 `project.yaml`，用的是插件模板 ✅ |

第一条说明：宿主那份模板**不会**让未装插件的用户误以为功能可用——
真正拦住他们的是 facade 的插件检查。

### 长期方向

把模板收敛到**一处**：宿主 `resolve_pdf_template` 改为经 facade 向插件要模板，
宿主不再自带副本。前提是确认"插件未装时不需要解析模板"——
目前 `ensure_pdf_project` 的顺序是"先解析模板 → 再调插件"，
要改成"先确认插件在处理"才安全。

**这个改动没做**，因为：收益是消除一份重复资源（约 7 KB），
风险是改动一条已在生产运行的解析链。记在这里待评估。

## 四、迁移暴露并修掉的既有问题（不是迁移引入的）

迁移让一批**原本就存在**的问题显形。记录在此，因为它们不是"迁移的副作用"，
而是迁移**顺带修好的**：

| 类别 | 问题 | 位置 |
|---|---|---|
| 安全 | 插件加载完全绕过验签（零签名即可执行任意代码） | `document_facade._plugin_handle` |
| 安全 | 验签与执行两次读盘，TOCTOU 窗口 | 同上 |
| 支柱 | `table_io` 被绑在插件上 ⇒ `omnicrawler transform` 未装插件时不可用 | `services/data_transform.py` |
| 支柱 | `compare_runs` 同上 ⇒ `compare-runs` 与删除审计不可用 | `document_facade.compare_runs` |
| 支柱 | `benchmark_convertx` 同上 | `tools/benchmark_convertx.py` |
| 能力判据 | 导航/首页/能力报告用"pdfplumber 装没装"当"PDF 可用"的代理 | `core/capabilities.py` 等 5 处 |
| 迁移遗漏 | LLM 出网丢 User-Agent（合规回退） | `pdfx/llm.py` |
| 迁移遗漏 | 5 处函数内导入指向已迁走模块 | GUI 视图 / `compat` |
| 迁移遗漏 | `selfcheck` 依赖清单只列 6 项（实际 24 项） | `plugin.py` |
| 用户面 | 文档教用户跑已移除的 `pdfx` 命令 | `INSTALLATION.md` / `OPERATIONS.md` |
| 用户面 | `omnicrawler pdf --help` 未装插件时抛 traceback | `cli/_main.py` |
| 依赖契约 | `pdf-write` / `excel` 两个 extra 未登记契约 | 3 处清单 |

**共同模式**：把"某个 Python 包在不在"或"某个模块在不在"当成"能力可用"的代理。
迁移后唯一可靠的判据是**插件装没装**。

## 五、迁移后的验证覆盖

| 层次 | 守卫 | 规模 |
|---|---|---|
| 代码完整性 | `test_migration_fidelity.py` | 54 模块逐行比对 |
| 插件模块级 | 插件仓 `tests/unit/` | 465 条 |
| 功能等价（API） | `tests/e2e/test_full_pipeline.py` | 4 条 |
| 功能等价（CLI） | `tests/e2e/test_cli_pipeline.py` | 2 条 |
| 安全 | 签名闸门 / TOCTOU | 5 条 |
| 可发布 | 目录布局 / 发布链路 | 8 条 |
| GUI 装配 | 导航 / 视图 / 能力 | 8 条 |
| 卸载态 | 每个 facade 入口均须可操作 | 24 条 |
| 支柱独立性 | 支柱①能力不得依赖插件 | 12 条 |
| 函数内导入 | AST + 真实 import 全量 | 153 处 / 2 条 |
| 依赖自检 | 覆盖完整性 + 降级行为 | 5 条 |
| CLI 用户面 | help/错误提示 | 3 条 |

汇总数字不手抄（会随时间失真）。要复核请直接跑：

```bash
# 宿主静态门禁（当前应为全绿）
python tools/self_verify.py --set static

# 宿主 tools + core 两个测试目录
python -m pytest tests/unit/tools tests/unit/core -q

# 插件仓全套件
cd ../OmniCrawler-market/submissions/plugins/4c3014804d85de2568f85e62a3048429/pdf-workbench
python -m pytest tests -q
```

> 已知环境限制：`test_shell_scripts_gate` 的 3 条 + `test_gate_registry` 的 1 条
> 需要 bash 创建命名管道，在受限沙箱里会失败（`NtCreateDirectoryObject ...
> 0xC0000022`）。这与迁移无关，换到普通桌面环境即通过。

## 六、仍未完成

- **插件未签名**：需要维护者身份密码（不由 AI 代输）。
  未签名时宿主 strict 策略会拒载，这是**正确行为**，不是故障。
- 真实桌面环境的 GUI 走查（当前只有 offscreen 验证）。
- **市场门 3 只检查 `plugin.py` 的导入图**，不看子模块——所以插件声明通过
  不代表依赖真的齐备。缓解：`selfcheck()` 已覆盖 24 个包并在缺失时报告。
  彻底修需扩展市场仓门禁，属规则变更，会影响现有 20 个投稿包，待评估。
