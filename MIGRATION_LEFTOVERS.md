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

## 四、迁移后的验证覆盖

| 层次 | 守卫 |
|---|---|
| 代码完整性 | `test_migration_fidelity.py`（54 模块逐行比对） |
| 功能等价（API） | `tests/e2e/test_full_pipeline.py`（4 条） |
| 功能等价（CLI） | `tests/e2e/test_cli_pipeline.py`（2 条） |
| 安全 | 签名闸门 4 条 + TOCTOU 1 条 |
| 可发布 | 目录布局 4 条 + 发布链路 4 条 |
| GUI | 装配 5 条 |
| 卸载态 | 24 条 |

## 五、仍未完成

- **插件未签名**：需要维护者身份密码（不由 AI 代输）。
  未签名时宿主 strict 策略会拒载，这是**正确行为**。
- 真实桌面环境的 GUI 走查（当前只有 offscreen 验证）。
