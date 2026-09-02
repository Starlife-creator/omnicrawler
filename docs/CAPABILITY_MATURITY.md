# 能力成熟度矩阵（v0.12.0）

> 本矩阵反映 OmniCrawler 0.12.0 的能力成熟度状态。

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
