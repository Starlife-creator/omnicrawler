# OmniCrawler 1.1.1 发行与验收报告

发行日期：2026-07-21

## 范围

1.1.1是1.1.0的安全与发布修复版本，不改变配置协议v5和插件API v1。它修复DNS安全检查与实际
连接之间的再次解析时间差，使robots.txt复用安全重定向路径，恢复Mypy门禁，并固定质量与构建依赖。

## 安全修复

- 直连Socket使用策略批准的地址字面量，原始Host和HTTPS SNI保持不变；
- 混合公网/私网DNS结果整体拒绝；
- robots.txt和相对重定向逐跳校验；
- 未配置代理时禁用环境代理继承；
- 显式代理的信任边界写入网络安全文档。

## 兼容性

- Python 3.10+；
- 配置协议仍为v5；
- 插件API仍为v1；
- 未知字段继续保留；
- 1.1.0工作区无需破坏性迁移；
- `allow_private_network`和`resolve_dns`显式例外继续支持。

## 验收结果

- Pytest：`99 passed, 2 skipped`；
- 新增安全基础测试：17项通过；
- Mypy 1.20.2：73个源文件零错误；
- Ruff 0.15.22：通过；
- 受控覆盖率77%，通过当前65%门禁；
- 176个Python文件编译通过；
- 67套模板通过，21项可选依赖导入成功；
- CLI版本与配置验证通过；
- wheel构建成功，SHA-256为
  `7faa428e1419888fba9117eb524b17631310fce919fead9b23c38fe971756161`；
- CycloneDX SBOM生成成功；
- PowerShell构建/安装脚本语法和GitHub Actions YAML通过；
- 普通回归中的浏览器用例按设计跳过；专用环境启用Playwright、Selenium、Chromium和
  ChromeDriver后，GUI与浏览器集成`4 passed`。

## 已知边界

- 显式代理负责代理侧目标DNS解析，应使用受组织控制且禁止内网跳转的代理；
- 浏览器、异步和流式协议将在1.2.0统一进入Egress Broker；
- 本源码验收不等于真实目标站点授权、OCR精度、长稳和高并发验收；
- Windows便携包需在目标构建环境生成并执行原生运行时测试；
- 当前本地机器没有Docker命令，Dockerfile已固定多平台digest并通过静态审查，但镜像构建必须由
  Docker CI补充验收。
