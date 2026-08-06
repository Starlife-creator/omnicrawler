# OmniCrawler 桌面运行、工作区与组件（0.4.0）

## Worker

桌面默认 `LocalWorkerBackend`，源码开发可显式使用 `InProcessBackend`。Worker 会话写入
`<workspace>/worker-session.json`，认证密钥不进入日志；Windows 使用命名管道，其他系统使用本地套接字，
不监听 TCP 端口。GUI 只轮询状态和发送控制命令，关闭 GUI 不会主动结束 Worker。

```powershell
omnicrawl worker -c task.yaml start
omnicrawl worker -c task.yaml status
omnicrawl worker -c task.yaml pause
omnicrawl worker -c task.yaml resume
omnicrawl worker -c task.yaml stop
```

## 工作区

目录包括 `config_versions/raw/attachments/rules/review/logs/output/snapshots/temp/components`，SQLite
`state.sqlite3` 是恢复权威。完整包包含工作区全部文件和 SQLite 一致性快照；support 包会脱敏且默认不含
raw；config 包只含配置。

```powershell
omnicrawl workspace -c task.yaml init
omnicrawl workspace -c task.yaml health
omnicrawl workspace -c task.yaml package --kind full --target project.zip
omnicrawl workspace -c task.yaml snapshot
omnicrawl workspace -c task.yaml rollback --target <workspace>/snapshots/snapshot-....zip
```

## 便携模式

- `PORTABLE.flag`：程序和数据都在应用目录；复制整个目录可迁移。
- `data-mode.json` mode=local：程序可复制，工作区保存在本机用户数据目录。
- mode=custom：工作区位于用户选定目录。

首次启动会询问。`${APP_DIR}` 和 `${DATA_DIR}` 可避免写死盘符。移动盘和网络盘会提示慢速数据库/OCR及
未完成写入时禁止弹出。

## Edition 与组件

Standard：GUI、CLI、Worker、HTTP/HTML/REST、Chromium/Playwright、PDF文本、基础导出。

Full：在 Standard 上增加 Selenium兼容、Tesseract、PaddleOCR离线模型、Office扩展、外部存储驱动和
开发者工具。AI、额外OCR语言、行业模板、数据库驱动和插件 SDK 可作为签名组件安装。

组件包 `component.json` 必须列出用途、版本、Edition、下载/磁盘大小、依赖、卸载影响和每个文件哈希；
正式包必须通过受信 Ed25519 公钥验证。本地开发包只有显式 `--allow-unsigned` 才能导入。
