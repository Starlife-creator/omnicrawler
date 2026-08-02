# OmniCrawler 2.6.0 可复用 E2E 测试包

本目录是一套独立、可复制的端到端验证包。它不访问互联网或生产数据；测试在临时目录中启动本地 HTTP 服务，并验证以下真实链路：

1. 静态页面抓取、PDF 下载、PDF 字段提取、结构化交付与再次运行去重；
2. CLI 配置校验与执行计划编译；可选 Chromium/Playwright 对动态页面的渲染、XHR 捕获与浏览器池复用。

## 在 GitHub Actions 中运行

`.github/workflows/e2e.yml` 会在 pull request、相关分支推送或手动触发时运行本套件。该工作流安装 Chromium，并上传 `e2e-artifacts`，其中包含 JUnit、Cobertura XML、Coverage JSON、文本覆盖率和 Markdown 报告。

## 手工运行

在仓库根目录运行下面任一命令。第一次运行需加安装开关：

```powershell
./e2e/run.ps1 -Install
```

```bash
./e2e/run.sh --install
```

可用 `-Browser` 或 `--browser` 启用本地 Chromium 扩展；不传该参数时核心套件不要求浏览器。`-FullRegression` 或 `--full-regression` 会在 E2E 前额外执行仓库的完整 pytest 回归。运行结束后，以下文件会生成在仓库根目录：

- `E2E_TEST_REPORT.md`：人可读结论、测试计数、覆盖率与门禁状态；
- `e2e-artifacts/`：JUnit XML、Coverage XML/JSON/文本报告和命令输出。

无论是否启用 `--full-regression`，脚本都会将 pytest 临时目录固定在 `e2e-artifacts/` 内，避免系统临时目录权限或遗留文件影响本地复跑。

## 覆盖率约定

本包分别报告两种覆盖率，避免混淆：

- **场景覆盖率**：两个已定义 E2E 场景的通过比例，目标 100%。
- **E2E 支撑代码行覆盖率**：`e2e.harness` 与 `e2e.render_report` 的真实 Coverage.py 行覆盖率，目标 95%。

95% 是严格门禁而非伪造指标。应用全源码覆盖率继续由项目既有质量工作流验证；少量 E2E 场景不能诚实地代表全部业务代码。若未达到，脚本会失败，但仍会写出完整报告和原始数据，供补充测试后复跑。
