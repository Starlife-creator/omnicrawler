# OmniCrawler 1.4.0 兼容性与回滚

- 配置 v5、Task IR v1、插件 API v1 保持兼容；工作区格式首版为 v1。
- 原 QProcess TaskRunner 源码保留作兼容实现，GUI 默认改用 LocalWorkerBackend。
- Worker 会话文件可删除而不删除任务 SQLite；无法重连时用 `recovery continue` 恢复断点。
- `portable.flag` 与新 `PORTABLE.flag` 均识别；未选择数据模式的冻结版默认保持便携行为。
- Standard 不再被错误要求通过 Selenium/PaddleOCR 冒烟；Full 仍执行完整浏览器和双 OCR 门禁。
- 升级前自动快照；升级器排除用户工作区。可回滚程序文件、配置快照和组件版本。
- Authenticode 仅在提供证书并实际运行 signtool 后声明通过；当前源码环境不包含发行证书。
