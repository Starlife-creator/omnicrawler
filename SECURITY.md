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
- 沙箱化文件/附件解压

完整的技术安全策略和合规要求请参阅：

→ [docs/SECURITY_AND_COMPLIANCE.md](docs/SECURITY_AND_COMPLIANCE.md)

## 支持的版本

| 版本 | 安全更新 |
|------|---------|
| 0.11.2（当前） | 活跃支持 |
