# OmniCrawler 4 配置迁移与回滚（前向兼容设计文档）

> **说明**：本文档是面向未来 OmniCrawler 4（下一个主版本）的前向兼容设计规范，描述 v4 将如何读取 2.x/3.x 配置。当前项目版本为 2.3.1，此文档供架构规划和兼容性测试参考，不影响当前版本运行。

OmniCrawler 4 默认直接读取 2.x/3.x 配置。加载时迁移只发生在内存中，原文件不会被改写；未知字段始终保留。

## 自动兼容规则

- `seed_urls` 复制到 `source.seeds`。
- `source.kind: rss` 规范为 `feed`。
- `output` 合并到 `outputs`。
- `crawl.delay_seconds` 在新位置缺失时复制到 `http.delay_seconds`。
- `plugin_paths` 复制到 `plugins.paths`。
- GUI 保存采用深层覆盖，只更新向导中实际编辑的值，模板元数据、分页、会话、插件、处理器和存储设置不会丢失。

## 显式生成新版副本

```powershell
omnicrawl migrate -c configs/old.yaml -o configs/old.v4.yaml
omnicrawl validate -c configs/old.v4.yaml
```

命令默认拒绝覆盖目标；只有明确使用 `--force` 才允许覆盖。

## 回滚

1. 停止当前任务；SQLite 状态和原始文件均留在原 workspace。
2. 重新使用升级前保留的原配置文件启动 3.x。
3. 如果 4.x 已写入新的状态字段，先复制整个 workspace 作为备份；3.x 不认识的新表不会影响已有表。
4. S3 镜像是附加副本，本地恢复副本默认仍存在。

发布包不自动删除旧模板、旧配置、会话或任务状态。
