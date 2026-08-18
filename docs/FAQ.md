# FAQ (常见问题)

## 安装与环境

**Q: 解压后双击没反应？**
A: 确保解压到普通可写目录（如 D:\OmniCrawler），不要直接在 ZIP 内运行。如仍无反应，运行 `OmniCrawler-Launcher.bat` 查看命令行报错。

**Q: 缺少 Chromium 或 Playwright？**
A: 便携版已内置。源码版运行 `python -m playwright install chromium`。

**Q: OCR 不可用？**
A: Standard 版不含 OCR 组件，下载 Full 版获取 Tesseract+PaddleOCR。文本 PDF 无需 OCR 也可提取。

**Q: 提示缺少 Python 或 CLI？**
A: Standard/Full 便携版无需安装 Python。源码版需 Python 3.10+ 并运行 `pip install -e ".[full]"`。

---

## 采集配置

**Q: 翻页但地址栏网址不变？**
A: 切换到浏览器模式，点击"学习点击/搜索/翻页"，操作一次翻页，系统自动捕获后台 API 接口。

**Q: 采集速度太慢？**
A: 增加 `concurrency` 到 4-8，减少 `delay_seconds` 到 0.5-1s。过高并发可能被限速或封 IP。

**Q: 被网站封了 IP？**
A: 增加 `delay_seconds` 到 2-3s，降低 `concurrency` 到 1-2。大网站建议使用官方 API。

**Q: 附件没下载？**
A: 确认 `download.enabled=true`，`extensions` 包含需要的文件后缀。无后缀文件按实际内容类型识别。

**Q: 字段提取为空？**
A: 检查页面内容类型（HTML/JSON），切换到对应 mode。动态页面用浏览器模式渲染后再提取。试跑查看原始响应。

---

## 运行与恢复

**Q: 任务中断了怎么办？**
A: 运行 `omnicrawler resume -c <config>` 从中断点继续。所有进度保存在 SQLite 中不丢失。

**Q: 改了提取规则如何重新导出？**
A: 运行 `omnicrawler reprocess -c <config>`，从原始归档重新提取和导出，不重新访问网站。

**Q: 如何定期自动采集？**
A: 专业模式下启用定时任务，或使用系统调度器（Windows 任务计划程序 / cron）执行 `omnicrawler schedule run-due`。

**Q: 如何复制项目到另一台电脑？**
A: 便携版复制整个文件夹。源码版复制 `work/<project>/` 目录，重新安装 omnicrawler 即可。

---

## 输出与导出

**Q: 为什么没有 Excel？**
A: 需要 `openpyxl`。便携版已包含；源码版 `pip install openpyxl`。

**Q: CSV 打开后中文乱码？**
A: Excel 中用"数据→从文本/CSV"选择 UTF-8 编码导入。建议同时导出 Excel 格式。

**Q: 如何导出到数据库？**
A: 支持 PostgreSQL（psycopg）和 DuckDB（duckdb），安装对应可选依赖后在 `outputs` 段配置。

---

## 安全与合规

**Q: 采集公开网站有什么规则？**
A: 默认遵守 robots.txt、只采同域名。确保有权访问，不绕过验证码和付费墙。

**Q: 凭据如何安全存储？**
A: 配置中用 `secret://name` 占位符，通过环境变量 `OMNICRAWL_SECRET_name` 注入。不写入 YAML。

---

更多帮助：`omnicrawler doctor`、F1 帮助中心、`docs/USER_GUIDE_2.0.md`
