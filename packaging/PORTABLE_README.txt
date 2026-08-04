OmniCrawler Windows 便携版
=========================

1. 请完整解压整个 OmniCrawler 文件夹，建议放到 D:\OmniCrawler。
2. 双击“OmniCrawler-Launcher.bat”，无需安装 Python 或浏览器。
3. 在第一步填写任务名称、业务目标和浏览器地址栏中的入口网址。
   如果翻页时地址栏不变，请点击“学习点击/搜索/翻页”，不要猜 URL。
4. 建议先点击“试跑检查”，确认少量样本正确后再正式运行。
5. 配置、日志、断点和结果默认保存在当前应用文件夹中。

请勿单独复制 OmniCrawler.exe 或 omnicrawl.exe。动态网页运行依赖同目录的
browsers 文件夹，帮助依赖 docs 文件夹。

Standard 版自包含 GUI、CLI、Python、HTTP/HTML、Playwright Chromium、PDF 原生
文本、Excel、异步请求和系统凭据接口，适合大多数网页/API/PDF 文本任务。

Full 版在 Standard 基础上增加 Selenium/ChromeDriver、PaddleOCR/Tesseract 离线 OCR、
Parquet/DuckDB、Redis/Scrapy、S3/PostgreSQL/OpenSearch 等完整客户端能力。

Redis、S3、PostgreSQL 和 OpenSearch 属于外部服务：客户端功能已随包提供；只有在您
选择这些后端时，才需要填写相应服务器地址和凭据。本地采集不依赖这些服务。
