# OmniCrawler 0.12.0 发布报告

> 发布日期：2026-08-26；配置协议：v5；公共 API：兼容。

## 发布结论

0.12.0 是一次**发布基础设施硬化**版本：不改变任务、工作区、证据或导出格式，
安全边界与可恢复语义保持不变。核心交付是"发布闸门三层漏斗"——单包超过
GitHub 单文件上限时不再劫持整次发布，以及版本更新工具在三处实战缺陷后的
全面修复。

## 已交付改进

### 发布闸门（三层漏斗，release.yml）

- **L0 构建门禁**：任一平台构建失败 ⇒ 聚合发布不启动（既有 needs 语义固化）。
- **L1 尺寸预分类**：三平台构建后本地比对 `ASSET_MAX_BYTES`（≈1.9 GiB），
  超限产物移出发布集并写入缺席清单；attestation 改 glob 自动适配。
- **L2 逐文件挂载**：Release 元数据与资产上传分离，按文件循环挂载——
  意外错误硬失败（fail-fast），仅预判的超限缺席允许降级；
  缺席时 Release 说明自动附带替代路径（Standard + components 加装 OCR）。
- **实战验证**：本版本构建中所有产物均在阈值内（Win Full 实测 1883 MB），
  闸门以"无事可做"形态全程通过。

### 版本更新工具（tools/bump_version.py）

- 修复 Windows GBK 控制台输出 `✓/→/─` 触发 `UnicodeEncodeError` 导致
  流程半途而废（输出流统一 UTF-8 reconfigure）。
- 修复 git 收尾对"不存在的可选版本化文档"整条 `git add` 失败、
  commit/tag 未创建的问题（仅暂存实际发生的重命名目标）。
- 新增 `--from-version` 断点续跑入口；CHANGELOG 区间旧版号由调用方
  传入，续跑不再产生空摘要。
- 0.12.0 本身即由修复后的工具端到端打标发布（GBK 控制台实测通过）。

### 缺陷修复

- User-Agent 测试的脆性子串断言（`"1.1" not in`）被合法新版本号击穿，
  改为与 `__version__` 精确相等。
- release 尺寸预分类在 macOS 使用 GNU 专属 `stat -c`，BSD stat 下失败，
  统一改用 Python 取文件大小。
- 资产挂载步骤 `cd` 出检出目录后 `gh` 无法定位仓库，显式 `GH_REPO` 修复。

### 文档与治理

- 透明性日志诚实降级为 informational-only（历史条目绝对路径脱敏）。
- 市场 README/CONTRIBUTING 信任模型与签名流程对齐双轨实态；
  SECURITY.md 密钥轮换/Shamir 表述改为规划态；CATALOG_SCHEMA 补
  `sequence`/`tombstones` 字段；市场 CHANGELOG 补记 0.5.0 → 0.6.0 结构变更。

## 升级与兼容

- 从 0.9.x/0.11.0 直接覆盖升级即可；配置与工作区格式无变化。
- 便携包仍分 Standard（GUI + Chromium）与 Full（另含 ChromeDriver 与
  双 OCR 引擎）；Windows Full 实测 1883 MB，处于发布闸门阈值内。
