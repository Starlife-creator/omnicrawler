# OmniCrawler 1.2.0 发行报告

发行日期：2026-07-22

## 交付结论

1.2.0 已完成统一 Egress Broker、七态运行状态机、阶段检查点、幂等导出、崩溃恢复、安全不变量
门禁和统一恢复中心的本地实现与验收。HTTP、异步 HTTP、robots、登录、浏览器、流式协议、AI、
插件及外部存储均经过统一出口，或以经过测试且可审计的 SDK/Selenium 例外明确呈现。

## 本地验证证据

| 门禁 | 结果 |
|---|---|
| 全量 Pytest | 169 passed，2 skipped |
| 真实浏览器与离屏 GUI | 4 passed（Playwright、Selenium、五步 GUI） |
| 全源码覆盖率 | 65.15%，门槛 60% |
| 安全与状态 | 89.47%，门槛 85% |
| 管线、HTTP与来源 | 75.05%，门槛 75% |
| 浏览器与 API | 81.57%，门槛 70% |
| PDF 与 OCR | 66.97%，门槛 65% |
| 桌面核心 | 61.29%，门槛 60% |
| Mypy | 76 个源文件零错误 |
| Ruff | 通过 |
| Python 源码编译 | 138 个文件通过 |
| CLI | 版本 1.2.0；security-report/recovery 帮助契约通过 |

普通全量回归中的两个真实浏览器用例按环境开关跳过，随后在指定 Chromium、ChromeDriver 和
`OMNICRAWL_BROWSER_TESTS=1` 的专用运行中通过，并追加到最终覆盖率数据。

## 制品

- `omnicrawl_platform-1.2.0-py3-none-any.whl`：390,913 字节；SHA-256
  `f17d11ee7912decb8a87faecbc13ff76aaf824f65e9091deca20b7545633a7dd`
- `omnicrawler-1.2.0-sbom.cdx.json`：34,965 字节；SHA-256
  `d666f8d86b66a069f10088ea289d4754dc6586d89428c6fef8e03fddd11a2c40`

## 明确边界与回滚

- Selenium 未拦截兼容模式必须显式授权；默认使用 Playwright。实验 BiDi 守卫仍标记实验性。
- S3、OpenSearch、PostgreSQL SDK 调用前经过策略、预算与审计，最终 Socket 仍由 SDK 控制。
- 配置协议保持 v5、插件 API 保持 v1；旧完成状态由兼容别名读取。
- 可保留 1.1.2 可执行文件直接回滚；恢复中心配置回滚会先保存当前配置。

## 当前环境未执行

- Docker 镜像构建（当前环境没有 Docker 命令）。
- Windows Standard/Full 最终便携包原生运行时验收。
- 托管 CI 中 Windows/Linux 与 Python 3.10/3.12/3.13 矩阵。

这些项目保持 pending，不以本地测试替代外部执行证据。
