# 2026-10-04 全面优化实施与验收

> 适用版本：0.15.0 · 配置协议：v5 · 维护状态：本轮完成记录；发布条件独立登记

本轮从 f3278e3 起，按维护者授权使用本机完整依赖验收，主程序无需便携包。旧计划和过时文档原字节归档于 `docs/archive/2026-10-04-before-comprehensive` 与工作区 `archive/2026-10-04-before-comprehensive`，归档摘要可核验。当前实施与验收证据位于工作区 `user-test-runs/comprehensive-optimization-20261004`；插件/模板开发位于 `market-plugin-and-template-development/comprehensive-20261004`。

## 已落地行为

| 用户遇到的场景 | 当前行为 | 实际证据 |
|---|---|---|
| 请求失败、429 或资源压力 | 生产主循环降低新请求认领量，稳定后恢复至配置上限，保留调整审计 | admission.xml、反向守卫 |
| 将分页、API、浏览器和附件任务交给别人复用 | 保留受支持的工作流语义；URL、动作值、载荷、凭据引用需重新填入，不复制会话秘密 | capture.xml、capture-pdf.xml |
| 不清楚任务需要验收哪些阶段 | Qt 逐阶段展示当前配置与验证建议，试跑摘要与证据完整性明确显示 | workflow.xml、workflow-diagnostics.png |
| 同一商品更名或时间戳变化 | 显式业务身份保持记录连续，噪声可忽略；不完整运行仍不判删除 | monitor.xml、scenes.xml |
| 同一模板的列表与详情互相报漂移 | 按同一页面 URL 比较历史基线 | page-diagnostics.xml |
| 交付 PDF 后需要引用原文 | 原生文字页/段定位、经核对的区域及表格行引用；缺失和扫描页显式标明 | documents.xml、pdf-quality.json |
| 想直接复用公告、商品监测或论文资料场景 | 内置模板与开发目录对应，固定真值覆盖交付、附件来源与本地分析 | scenes.xml、scene-result.json |
| 市场条目无法判断交付内容 | 场景卡显示输入输出、权限、组件和限制，验证证据按哈希检查 | market.xml、scenario-cards.json |
| 显式 HTTP 代理在 Windows 被注册表规则绕过 | 使用显式代理，保留环境 no_proxy 语义；未配置代理仍不继承环境代理 | proxy-final.xml |
| 本机 Selenium BiDi 挂起 | 失败关闭出口；公开任务可显式选择同策略 Playwright 回退，记录实际引擎；持久登录状态拒绝回退 | browser-guards-final.xml、real-browser-final.xml、renderer.json |
| OCR 依赖是否能独立交付 | 冻结 Tesseract 入口、原生模型/运行库与隔离测试签名组件实际完成 OCR，篡改执行前拒绝 | native-component-ocr.json |

实现借鉴和边界沿用 [生态观察](ECOSYSTEM_OBSERVATION.md) 与 [融合研究](RESEARCH_AND_FUSION.md)。本轮资源反馈思路对照 [Scrapy AutoThrottle](https://docs.scrapy.org/en/latest/topics/autothrottle.html) 和 [Crawlee AutoscaledPool](https://crawlee.dev/js/api/core/class/AutoscaledPool)，没有宣称穷尽市场所有项目。

## 本机验收结论

CLI 固定真值、真实 Playwright 渲染/分页/滚动、PDF/表格/真实 Tesseract、Qt 自动运行、市场签名安装及插件契约的最终分组回归通过。记录测试结果只说明验证范围，不用测试数量表示优化成效。跳过项按 JUnit 与原始日志登记，不算通过；最终代码的页面基线修改另有定向补验与完整静态门禁。

曾尝试单进程合跑单元与集成，Qt 样式初始化发生 Windows access violation，原始 full-regression.log 保留。后续使用独立进程分组完成验证；共享 Qt 状态污染只是推测，确切根因仍未关闭。Qt 截图为实际 offscreen 窗口，不等同于完整人工桌面走查。原生 Selenium 验收实际触发 Playwright 回退，不计为原生引擎问题解决。类似上游现象可见 [Selenium issue 17373](https://github.com/SeleniumHQ/selenium/issues/17373)，不能据此断言本机问题的确切根因。

## 测量结果与发布边界

固定 HTTP 样本中固定/自适应吞吐约 30.33/30.30 页每秒，无提速证据。OCR 组件约 43.76 MiB 压缩、92.72 MiB 安装；相同 PNG 的单次直接/组件路径约 3.70/4.87 秒、202.82/205.95 MiB 总进程树 RSS，没有总体节省内存结论。

OCR 组件是 Windows 开发试点，使用隔离测试公钥，不是正式市场签名发布。完整 DLL 许可归属、干净机器断网、搬迁、三平台发布与主程序便携包大小未验证。真实网站登录、外部模型费用、完整 A—G 人工走查未测。既有签名产物和其他功能目录保留，未自动推送或发布。

完整结果、复现入口、失败处理和证据清单见工作区 `user-test-runs/comprehensive-optimization-20261004/验收报告.md`。
