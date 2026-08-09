# ADR-005：AppConfig 与 CrawlConfig 字段差异审计

> 状态：草稿 | 日期：2026-07-27

## 背景

OmniCrawler 存在两个配置模型：

- `config.py::AppConfig` — 运行时核心配置（被 pipeline、fetcher、exporters、state 消费）
- `gui/core/config_model.py::CrawlConfig` — GUI 向导配置模型（被 5 个 Wizard 页面和 serializer 消费）

两者独立定义，新增字段需要在两处同时添加。本文档标注字段归属关系。

## 字段映射表

### 项目元数据

| AppConfig 字段 | CrawlConfig 字段 | 归属 |
|---------------|-----------------|------|
| `path: Path` | `_config_path: Path\|None` | 共享 |
| `root: Path` | `project_dir: Path` | 共享（命名不同） |
| `workspace: Path` | `workspace: Path` | 共享 |
| `raw: dict` | （无直接对应，由 serializer 处理） | **AppConfig 专属** |
| `migration_notes: tuple` | （无） | **AppConfig 专属** |
| — | `_unsaved: bool` | **CrawlConfig 专属** |

### 任务标识

| AppConfig（raw 内） | CrawlConfig | 归属 |
|---------------------|------------|------|
| `raw["name"]` | `task_name: str` | 共享 |
| — | `template_id: str` | **CrawlConfig 专属** |

### 数据源

| AppConfig（raw 内） | CrawlConfig | 归属 |
|---------------------|------------|------|
| `raw["source"]["kind"]` | `source_kind: str` | 共享 |
| `raw["source"]["seeds"]` | `seeds: list[str]` | 共享 |
| `raw["source"]["site_type"]` | `site_type: str` | 共享 |

### 采集范围

| AppConfig（raw 内） | CrawlConfig | 归属 |
|---------------------|------------|------|
| `raw["source"]["pagination"]` | `pagination: dict` | 共享 |
| `raw["source"]["max_pages"]` | `max_pages: int` | 共享 |
| `raw["source"]["concurrency"]` | `concurrency: int` | 共享 |
| `raw["source"]["delay_seconds"]` | `delay_seconds: float` | 共享 |
| `raw["source"]["timeout_seconds"]` | `timeout_seconds: int` | 共享 |

### 字段定义

| AppConfig（raw 内） | CrawlConfig | 归属 |
|---------------------|------------|------|
| `raw["fields"]` (list of dict) | `fields: list[FieldDef]` | 共享 |
| — | `FieldDef.required: bool` | **CrawlConfig 专属**（GUI 标记） |
| — | `FieldDef.sample_value: str` | **CrawlConfig 专属**（GUI 示例） |

### 筛选与附件

| AppConfig（raw 内） | CrawlConfig | 归属 |
|---------------------|------------|------|
| `raw["filter"]` | `topic_filter: dict` | 共享 |
| `raw["download"]["enabled"]` | `download.enabled: bool` | 共享 |
| `raw["download"]["extensions"]` | `download.extensions: list[str]` | 共享 |

### 输出

| AppConfig（raw 内） | CrawlConfig | 归属 |
|---------------------|------------|------|
| `raw["output"]["format"]` | `output_formats: list[str]` | 共享（命名不同） |
| `raw["output"]["dir"]` | `output_dir: str` | 共享 |

### AI 配置

| AppConfig（raw 内） | CrawlConfig | 归属 |
|---------------------|------------|------|
| `raw["ai"]["enabled"]` | `ai_enabled: bool` | 共享 |
| `raw["ai"]["provider"]` | `ai_provider: str` | 共享 |
| `raw["ai"]["model"]` | `ai_model: str` | 共享 |
| `raw["ai"]["api_key"]` | `ai_api_key: str` | 共享 |
| — | `ai_allow_dom_content: bool` | **CrawlConfig 专属** |
| — | `ai_allow_screenshot: bool` | **CrawlConfig 专属** |
| — | `ai_allow_pdf_content: bool` | **CrawlConfig 专属** |

## 差异统计

| 类别 | 数量 |
|------|------|
| 共享字段 | ~30 |
| AppConfig 专属 | 2（`raw`, `migration_notes`） |
| CrawlConfig 专属 | ~10（UI 状态字段 + 精细权限控制） |
| 命名不同但语义相同 | 2（`root`↔`project_dir`, `output.format`↔`output_formats`） |

## 结论

1. **不做合并**。两模型职责不同：AppConfig 是运行时真相源，CrawlConfig 是 GUI 视图 + UI 状态。
2. **命名对齐优先**。`root↔project_dir` 和 `output_formats` 命名差异是纯粹的遗留问题，应统一。
3. **新字段决策树**：
   - 运行时需要的 → 入 `raw`（AppConfig）
   - GUI 临时状态（unsaved/样本值/标记） → 入 CrawlConfig
   - 运行时 + GUI 都需要的 → 入 `raw`，CrawlConfig 通过 serializer 读写
4. **未来考虑**（低优先）：添加 `CrawlConfig.to_app_config()` / `from_app_config()` 双向转换方法，使字段映射在代码中可执行而非纯文档。
