# OmniCrawler SDK 1.6 Preview

入口：`omnicrawl.sdk`。

- stable：`TaskSpec`、`TaskIR`、`TaskPlan`、`validate`、`compile`。
- preview：`run`、`query`、`DatasetReader` 及 Source/Fetcher/Extractor/Processor/Exporter/CredentialProvider 协议。
- internal：GUI widget、SQLite连接、Pipeline具体类和 Worker IPC 实现，不能由第三方依赖。

Preview 破坏性变化会记录迁移说明；进入 stable 后遵循语义化版本，至少经过一个小版本弃用期。正式运行默认要求当前计划与最近试跑哈希一致。
