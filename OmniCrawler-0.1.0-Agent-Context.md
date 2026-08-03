# OmniCrawler 0.1.0 项目上下文

## 产品目标

OmniCrawler 是面向桌面与单机生产环境的数据采集与文档抽取平台。用户应能从一句任务描述开始，受控地完成目标网站、API、动态页面或 PDF 的采集、提取、复核与导出，并能在中断后恢复任务。

## 当前版本事实

- 版本：0.1.0；Python：3.10+；配置协议：v5；公共 SDK 和 CLI 语义保持兼容。
- GUI 简单模式的首页是主要任务入口：自然语言描述优先，所有试跑前必填项前置；解析结果必须可见、可编辑、可拒绝。
- GUI 生命周期必须安全：通知有计时器兜底，转场会在销毁时停止，主窗口关闭先取消并等待工作线程结束。
- 持久状态使用 SQLite WAL；任务请求、范围、robots、预算、凭据和审计边界不能因便利性被绕过。

## 架构边界

1. 站点差异放入 source、template、fetcher 或 extractor，不在 Pipeline 编排层堆叠网站特例。
2. 任何任务网络访问通过 EgressBroker，保留范围、预算、凭据作用域和审计。
3. AppConfig 是运行时真相源，CrawlConfig 是 GUI 视图模型；配置迁移必须向后兼容。
4. GUI、CLI 和 SDK 都应生成同一种可解释 Task IR/计划，再进入 Pipeline。
5. 后台工作不能触碰已销毁 Qt 控件；取消、进度和关闭操作必须可重复调用。

## 发布原则

- 不修改历史发布报告、保留版构件或运行中的其他目录。
- Windows 便携版使用 `build_windows.ps1 -Offline`，复用已验证的 `.venv`、浏览器与运行时缓存，但总在新的版本化输出目录中构建。
- 发布前验证版本一致性、全项目测试、源码 ZIP、wheel、Standard/Full 便携目录与 ZIP；校验完成后才删除被明确授权回收的旧构件。

详见 `docs/ARCHITECTURE.md`、`docs/CONFIG_REFERENCE.md`、`docs/GUI_DESIGN_2.1.md`、`docs/OPTIMIZATION_PLAN_FIRST_PRINCIPLES_0.1.0.md` 和 `docs/releases/RELEASE_REPORT_0.1.0.md`。
