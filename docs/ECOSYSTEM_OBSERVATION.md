# OmniCrawler 生态观察清单（Ecosystem Observation List）

> 对应 [RESEARCH_AND_FUSION.md](RESEARCH_AND_FUSION.md) 的「持续观察 / 计划借鉴」行。
> 状态由 `omnicrawl doctor` 自动校验：标「✅ 已融合」的行，其落点模块必须真实存在，
> 防止文档与代码漂移（见 `services/doctor.py::check_ecosystem_doc`）。

## 状态机

```
观察中 → 评估中 → 计划借鉴 → 已融合 ✅  /  拒绝 ❌（红线冲突）
```

- **观察中**：仅记录，不投入实现。理由写在本行。
- **评估中**：方法论/模块已确定可复用，等待排期。
- **计划借鉴**：已明确落点（`模块路径（计划新建）`），未开工。
- **已融合 ✅**：落地完成。doctor 校验落点模块存在。
- **拒绝 ❌**：与合规红线冲突，见 RESEARCH_AND_FUSION.md「不吸收的能力」。

## 采集内核与浏览器

| 状态 | 资源 | 关注重点 | 落点模块 | 备注 |
|---|---|---|---|---|
| 观察中 | awesome-web-scraping | 通用爬虫生态分类法 | `sources/` `fetching/` | 持续观察，无排期 |
| 观察中 | awesome-ai-web-scraping | AI 驱动爬虫新范式 | `extraction/ai_graph.py` `extraction/intelligent_scraper.py` | 持续观察 |
| 观察中 | Awesome-Web-Scraping（中文） | 中文社区实践 | `templates/` | 持续观察 |
| ✅ 已融合 | Scrapling | 元素结构指纹 + 置信度分级 | `core/structure_fingerprint.py` `extraction/adaptive_extractor.py` | P2-1；选择器自愈仍在观察 |
| ✅ 已融合 | Botasaurus | 浏览器 Profile 持久化 | `fetching/profile_registry.py` `fetching/browser_fetcher.py` | P2-2 |
| ✅ 已融合 | Colly | 域名独立并发配额 + hook 点 | `fetching/domain_semaphore.py` `fetching/hooks.py` `fetching/async_fetcher.py` | P2-3 |

## 数据清洗与提取

| 状态 | 资源 | 关注重点 | 落点模块 | 备注 |
|---|---|---|---|---|
| ✅ 已融合 | llm-tab-cleaner | 规则失败→LLM 影子修复→复核 | `quality/shadow_repair.py` `quality/auto_apply.py` `quality/llm_candidate_generator.py` `quality/observation_store.py` | P3-3 L2 观察期/L3 持久化 |
| ✅ 已融合 | AutoDataCleaner | 类型推断 + 分级修复（L1 幂等 / L2 规则；L3 LLM 槽位默认关） | `quality/normalizers.py` | 无损性硬约束：推断失败/混合类型不猜 |
| ✅ 已融合 | datatoolkit / Sieve | 流式数据管道、算子组合 | `services/data_transform.py` `commands/transform.py` | P3-2（`omnicrawl transform` 值级变换，写盘需 --confirm） |
| ✅ 已融合 | VERT | 格式注册表 + 最短路径图搜索 + 零信任 | `convertx/paths.py` `convertx/__main__.py` | P3-2（--list-paths 路径枚举 + 注册表驱动） |
| ✅ 已融合 | everythingtohtml | 统一文档中间表示（txt/html/eml/docx/pptx/odt/epub → IR → 槽位抽取/文本导出） | `document_ir/` `convertx/document.py` `doc_extractors/` `core/encoding.py` `sources/url_cleaner.py` | S1-S3：文本/Markdown 导出 + 槽位抽取 auto 打通 + 编码自动检测 |
| 计划借鉴 | 正文容器/Elementor 类名词典 | 整页 HTML 正文主体识别（class/id 白名单 → 文本密度兜底） | `document_ir/parsers.py`（html 正文抽取增强，计划） | 待激活：无消费者不建配置；落点=html 正文抽取 / L3 内容级嗅探 / 通用正文选择器兜底 |

## 文件转换

| 状态 | 资源 | 关注重点 | 落点模块 | 备注 |
|---|---|---|---|---|
| ✅ 已融合 | ConvertX | 统一进度事件 + 幂等重试 + 5×5 互转 | `services/progress.py` `convertx/` | P2-4 + P3-2 + CLI `python -m omnicrawl.convertx` |

## 配置与网址

| 状态 | 资源 | 关注重点 | 落点模块 | 备注 |
|---|---|---|---|---|
| ✅ 已融合 | Repo Swap / Domain Swapper | 域名映射表 + 环境隔离别名 | `core/site_aliases.py` | P2-5 |
| ✅ 已融合 | Dev-Sidecar / FastGithub | 多节点健康路由 + 镜像组透明转发 | `sources/mirror_registry.py` `fetching/async_fetcher.py` | P3-1 |

## 持续观察（不设落点，仅方法论追踪）

- 选择器自愈（Scrapling 借鉴）：`extraction/adaptive_extractor.py` 结构指纹已融合，
  自愈重定位仍在观察期，待漂移检测数据积累后再评估。
- 生态分类法 / AI 爬虫新范式 / 中文社区实践：无实现排期。
