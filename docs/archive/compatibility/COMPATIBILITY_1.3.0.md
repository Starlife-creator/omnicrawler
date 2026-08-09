# OmniCrawler 1.3.0 兼容性

- v5 YAML 是持久化兼容格式；Task IR v1 是中间格式，两者可双向转换并保留未知字段。
- `TaskSpec`、五步向导、模板目录、录制 JSON、API发现 JSON 与手工 YAML 均保留，统一汇入 IR。
- 计划哈希会随非敏感执行语义改变；密码、token、secret 与 api_key 的具体值不会改变哈希。
- GUI 主窗口新增控制器装配但不改变保存、历史恢复、问号帮助或模式切换契约。
- CLI 原命令保留；新增 `plan`。运行、试跑、控制与导出改经 Application Service，输出仍为 JSON。
- 回滚到 1.2.0 时继续使用原 YAML；`plan-bindings.json` 可被旧版忽略。
