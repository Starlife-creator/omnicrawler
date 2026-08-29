# 分布式与外部服务扩展

0.11.2 的首选运行形态仍是单机 SQLite。分布式组件用于容量扩展，不是基础功能的前置条件。

## Redis frontier 与锁

安装 `.[distributed]` 后使用 `omnicrawler.redis_frontier.RedisFrontier`。实现提供去重入队、claim/ack/fail 和带租约的 acquire/release lock；调用方必须保存锁 token，并只释放自己的锁。

生产要求：

- Redis 启用认证、TLS、持久化和内存上限。
- 队列 key 使用项目命名空间。
- worker 失败后有 pending/reclaim 策略。
- 站点级限速不能因增加 worker 被绕过。

## Scrapy worker

`source.kind: scrapy` 通过桥接运行已有 spider。Scrapy 的并发、重试和 middleware 由其配置控制；OmniCrawler 负责项目入口和交付衔接。不要让两个运行器同时写同一 SQLite 工作区。

## 外部数据层

- S3：原始对象镜像，仍保留本地副本。
- PostgreSQL：结构化记录 JSONB 镜像，不承担本地 frontier 恢复。
- OpenSearch：搜索索引镜像。
- Parquet/DuckDB：分析交付，不是事务状态库。

## 调度

本地 ScheduleStore 使用 SQLite 租约，允许多个系统触发器争抢而不重复运行同一到期项。跨机器工作流可通过插件或外部编排器调用 CLI，但应保持单个配置/工作区的所有权和审计边界。

## 扩容顺序

1. 优化模板和 HTTP 优先路由。
2. 调整单机 HTTP 并发与浏览器池。
3. 分项目/域名拆分工作区。
4. 引入 Redis/Scrapy worker。
5. 引入外部对象与结果存储。

每一步都重新核对目标站点速率、数据一致性、重试风暴和恢复演练。
