# OmniCrawler 1.2.0 兼容性与迁移

- 配置协议仍为 v5；旧配置无需手工迁移，新增 `egress` 使用安全默认值并保留未知字段。
- 插件 API v1 继续接受 `register(registry)`；申请网络权限的插件可升级为
  `register(registry, context)` 并通过 `context.network` 访问声明域名。直接网络库导入现在会被拒绝。
- 运行完成状态规范化为 `succeeded`；读取和比较仍接受旧数据库中的 `completed` 等别名。
- Playwright 是受控浏览器默认实现。Selenium 若未显式选择实验守卫或未拦截兼容模式会安全拒绝，
  这是有意收紧的安全行为。
- SQLite 仍是本地恢复权威；S3、PostgreSQL、OpenSearch 接口保持兼容，但现在经过统一出口审计。
- 回滚：保留 1.1.2 可执行文件和配置；1.2.0 新增表不会破坏旧表。使用恢复中心的
  `rollback-config --backup` 时会先保存当前配置副本。
