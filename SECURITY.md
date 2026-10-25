# 安全策略

## 报告安全漏洞

如果您发现安全漏洞，请不要在公开 Issue 中报告。请使用 GitHub Private
Vulnerability Reporting（仓库 Settings → Security 已开启）：

→ https://github.com/Starlife-creator/omnicrawler/security/advisories/new

我们承诺在 90 天披露期限内处置。若 PVR 不可用，可改用
zqx666666@tutamail.com 直接联系维护者（邮件同样遵守披露期限）。

## 安全架构

OmniCrawler 内置多层安全防护，包括：

- 网络访问边界控制（仅 HTTP/HTTPS，防 SSRF）
- Egress Broker 统一出站策略
- 凭据脱敏与密钥管理
- 安全归档解压（路径穿越、符号链接、压缩比与体积上限校验）

### 插件隔离当前边界（请勿误解为 OS 级沙箱）

当前版本插件隔离由以下四层构成，**不等同于** AppContainer / Landlock /
seccomp / Seatbelt / Windows Job Object 等 OS 级 confinement：

1. **子进程边界**：插件在独立子进程中执行，宿主解释器与依赖不注入；
2. **导入隔离**：源码模式以 `python -I -S` 启动，冻结模式使用最小宿主 exe；
3. **权限代理**：manifest 权限白名单 + 批准审批，能力经宿主代理桥接；
4. **静态预检**：加载前完成清单、签名与 AST 静态检查。

OS 级隔离（Linux Landlock/seccomp、Windows AppContainer/Job Object、
macOS Seatbelt）列入 Roadmap，待对应平台 CI 矩阵验证后接入；在此之前不可将
当前子进程边界对外表述为「OS 沙箱」。逃生开关 `sandbox_escape`（关闭 OS 层，
仍保留子进程边界）与遗留 `legacy_in_process` 进程内路径均为显式降级动作，
会写入审计台账并在日志中横幅告警，默认路径 fail-closed。

### 任务配置信任边界（A-P3-18）

任务配置（含 `source.follow_xpath`、提取器表达式、请求模板等）由**操作者本人
编写**，配置文件写入本地即视为受信；运行期对这些表达式不再做二次沙化。当前
版本不直接加载远程 / 市场来源任务配置；若未来支持远程或市场来源，其表达式必须
先经表达式节点白名单校验（`core/ast_evaluator` 白名单集），校验通过前不得进入
链接发现、提取器或求值执行路径。

完整的技术安全策略和合规要求请参阅：

→ [docs/SECURITY_AND_COMPLIANCE.md](docs/SECURITY_AND_COMPLIANCE.md)

## 支持的版本

| 版本 | 安全更新 |
|------|---------|
| 1.0.1（当前） | 活跃支持 |
