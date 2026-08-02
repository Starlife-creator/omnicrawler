# OmniCrawler 2.7.0 生产部署指南

## 推荐拓扑

单机优先使用 Python 3.12、独立虚拟环境、SQLite WAL 和本地 raw 副本。只有数据量或吞吐确实超出单机时，再启用 Redis frontier、Scrapy worker、S3、PostgreSQL 或 OpenSearch；外部组件不替代本地恢复状态。

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -U pip
.venv\Scripts\python -m pip install -e ".[html,pdf,async,browser,gui,security,storage]"
$env:PLAYWRIGHT_BROWSERS_PATH=(Join-Path (Get-Location) '.playwright-browsers')
.venv\Scripts\python -m playwright install chromium
omnicrawl doctor -c configs/project.yaml
omnicrawl --log-format json run -c configs/project.yaml --max-pages 20
```

## 凭据

```yaml
source:
  headers:
    Authorization: secret://api_authorization
```

用 `OMNICRAW_SECRET_API_AUTHORIZATION` 或系统 keyring 提供值。CI、日志、诊断包、模板和交付压缩包不得含生产凭据。Cookie 默认不持久化；确需续跑时设置具名 session 并限制工作目录权限。

## 数据层

- `state.sqlite3` 和本地 raw 需要可靠磁盘与备份。
- S3 镜像启用后仍保留本地副本。
- PostgreSQL/OpenSearch 设置 `fail_open: true` 可避免镜像故障破坏采集；严格一致性交付才使用 false。
- 用 retention 策略和 `cleanup` 控制空间，执行前始终预览。

## 容量与故障

- 为磁盘保留至少 2 GiB 或任务估算量的 10%。
- 先用并发 1–4；遵从目标 API 的速率头和 Retry-After。
- 浏览器池通常小于 HTTP 并发；每个上下文保持会话隔离。
- 定期演练网络中断、磁盘不足、外部存储不可用、进程终止、规则变更后 reprocess。

## 可观测性

采集 JSON 日志、`metrics.prom`、`pipeline_summary.json` 与 diagnostics。告警至少覆盖：任务失败、资源限制、连续 fetch 错误、质量完整率下降、frontier 长时间不收敛、存储 warning 和 PDF/OCR 失败。

## 发布

发布物应包含源码包、wheel、SBOM、验证报告和变更记录。生产升级先复制工作目录，在副本执行 `omnicrawl migrate` 和小样本回归；不要直接覆盖唯一状态库。
