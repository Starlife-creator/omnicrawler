# 能力成熟度矩阵（v0.15.0）

> 适用版本：0.15.0 · 配置协议：v5 · 维护状态：现行

> 本矩阵反映 OmniCrawler 0.15.0 的能力成熟度状态。

成熟度含义：

- **Stable**：持续集成和离线场景验证完整，承诺兼容；公共 API 语义化版本维护。
- **Preview**：主要实现和测试存在，可在小版本中演进，但必须给出迁移说明；人工批准、权限和边界不可跳过。
- **Reserved**：预留接口，不作为已交付能力，当前版本不提供服务端控制面。

| 能力 | 等级 | 边界 |
|---|---|---|
| TaskSpec/IR/Plan、统一 Egress、七态恢复 | Stable | 安全策略默认开启且不可关闭；Egress Broker 统一网络安全策略 |
| 持久事件循环、批量 DB 操作、连接池化 | Stable | 当前版本优化：异步组件复用持久事件循环，DB 批量（executemany/preload/pipeline），S3/HTTP 连接池化 |
| BrowserAction + BrowserEngine Protocol | Stable | 统一浏览器操作协议（PlaywrightAdapter/SeleniumAdapter） |
| 共享重试配置 | Stable | 统一 parse_retry_config()，不在 http_client 和 async_fetcher 分别内联 |
| 任务工作台、首页、模板、PDF/OCR、监测、导出 | Stable | OCR 质量取决于版式/组件 |
| 专业复核、证据账本、Schema 契约 | Stable | 业务契约需项目维护者定义 |
| 配置 v1-v5 迁移、未知字段保留 | Stable | 有往返和迁移测试；旧配置可升级，不做破坏性丢弃 |
| 静态 HTML、REST、Sitemap、Feed | Stable | 仍需按目标站点条款试跑 |
| 原始归档、SQLite WAL 状态、重处理 | Stable | 单机本地权威状态；SQLiteRunRepository 已标记 DeprecationWarning |
| 安全压缩包解压、凭据作用域、诊断脱敏 | Stable | 默认安全策略开启；凭据作用域和熔断 |
| SDK run/query 与扩展协议 | Preview | 按弃用期演进；stable 接口按语义化版本维护，删除前至少一个小版本弃用期 |
| 隔离插件、影子修复、自适应执行 | Preview | 人工批准、权限和边界不可跳过；插件在隔离沙箱/子进程中运行 |
| 远程 Worker/团队编排 | Reserved | 当前版本不提供服务端控制面 |

> "支持多种网站"表示具有多协议、浏览器回退和模板扩展能力，不表示任何网站都无需配置或授权即可采集。

## 2026-10-04 本机 Full 补证

| 能力 | 等级 | 已验证与限制 |
|---|---|---|
| 生产自适应请求认领 | Preview | 实际 HTTP 反馈、资源压力和有界审计；固定样本未证明吞吐提升 |
| 分页/API/浏览器/附件工作流捕获与阶段诊断 | Preview | 保留受支持语义、秘密参数重填；阶段显示配置，不等同于试跑通过 |
| 显式业务身份与噪声过滤 | Preview | 同一业务记录修改与无变化周期；原有不完整运行删除保护继续生效 |
| PDF 段落区域、表格与事实定位 | Preview | 原生文字逐段核对及表格行引用；扫描文档分析不自动启动 OCR |
| 市场场景说明卡 | Preview | 有界显示与证据哈希校验；不替代签名、权限审批或真实站点验证 |
| 公告、商品变化、论文资料模板 | Preview | 本地固定样本实际交付与分析；目标站点仍需配置、授权和试跑 |
| 原生 Tesseract 独立 OCR 组件 | Preview | Windows 本机实际冻结入口、测试信任安装、OCR 真值及篡改拒绝；正式签名、干净机器、跨平台和完整 DLL 许可归属未验收 |

证据与分组回归见 [本轮实施记录](COMPREHENSIVE_OPTIMIZATION_2026-10-04.md)。真实 Selenium 验收实际使用显式 Playwright 回退，不计为原生 Selenium 子请求拦截已解决。
