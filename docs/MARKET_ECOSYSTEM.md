# 市场生态远期设计（Helios 蓝图适配版）

本文档记录 OmniCrawler 市场生态的**远期设计蓝图**，是对 Helios 蓝图（三库架构、
市场镜像、签名基础设施）的适配与落地方案。**当前阶段不实施**，仅作为生态扩展时的
决策依据。已落地的能力见 `docs/ADR-001-plugin-catalog.md`（git-as-registry、
身份系统、三层信任）。

---

## 1. 生态演进路线

| 阶段 | 状态 | 说明 |
|------|------|------|
| git-as-registry（单仓内） | ✅ 已落地 | `registry/` 自包含：plugins/authors/keys/tools + CI 门禁 |
| 拆库（registry 独立仓库） | 📋 随时可做 | 复制 `registry/` + CI 文件 + 改 `catalog_url`（3 步） |
| 市场服务器（market-server） | ⏳ 远期 | 社区贡献者增多、需要审核流水线/徽章/统计时 |
| 官方站点/托管 | ⏳ 远期 | 插件包 CDN + 维护者签名管道 |

---

## 2. 三库架构（对齐 Helios §18）

| 仓库 | 许可证 | 职责 | 密钥 |
|------|--------|------|------|
| `omnicrawler`（主仓库） | Apache 2.0 | 桌面应用 + 引擎 + 市场客户端（仅 API 调用）+ 内置维护者公钥 | 仅公钥 |
| `omnicrawler-market-server` | AGPL-3.0 | 市场 API + 管理后台 + 审核流水线 + 签名管道代码（调用离线 HSM 的逻辑开源） | 无 |
| `omnicrawler-plugins`（registry） | CC0 | 插件元数据仓库，Git 即注册表，PR 提交插件清单 | 无 |
| 离线（不在任何仓库） | — | 维护者签名私钥（离线 HSM / 加密 USB Key） | 私钥唯一存放处 |

**关键决策**：market-server **不做就不做，做就开源（AGPL-3.0）**——借鉴 F-Droid，
任何人都可自建市场镜像；官方市场不是唯一入口，P2P 分发是合法补充。

## 3. 市场镜像与信任传递（对齐 Helios §21.3）

| 市场源 | 签名 | 信任 |
|--------|------|------|
| 官方市场 | 维护者私钥（离线 HSM） | 自动信任（层级 1） |
| 社区镜像 | 不重签，保留原始 `plugin.py.sig` | 信任等级不变 |
| 私有市场 | 企业自签 | 需用户信任企业公钥 |
| 离线镜像 | 原签名不变 | 保留原始信任等级 |

镜像信任传递原理：镜像只复制插件包与签名文件，官方签名的插件在镜像中仍携带
`plugin.py.sig`，验签结果不变。

**客户端配置**：`plugins.catalog_url` 已是单一可迁移配置点；多市场源列表
（`market_sources.json` 形式）待 market-server 落地时一并实现。

## 4. 用户名处理（对齐 Helios §13.9）

- **唯一标识 = 公钥指纹**（SHA-256 前 16 字节 hex），用户名仅用于显示。
- git-as-registry 模式下 `authors/<username>.yaml` 文件名天然唯一；同名用户
  的 `display_name` 带 `-NN` 后缀（CI 已校验连续性与先注册者保留原名，见
  `registry/tools/generate_catalog.py::_check_display_name_suffixes`）。
- 后缀仅在市场分发时生效；P2P 信任提示显示创作者本地原始用户名，唯一标识始终是
  公钥指纹。

## 5. 签名基础设施运维（对齐 Helios §21.5）

- **密钥隔离**：维护者私钥只存离线 HSM / 加密 USB Key，从不接触网络；签名管道
  代码开源（market-server 的 `src/signing/` 定义 `trait Signer`，实现部署在离线机）。
- **签名可审计（远期蓝图）**：目标态为带哈希链的透明性日志，可由第三方验证
  "未被事后删改"。当前 `tools/sign_plugin.py` 写入的 `signing_transparency.jsonl`
  为 **informational-only** 追加日志——无防篡改能力、无消费者校验，仅作人工
  回溯线索；路径字段已脱敏为相对/文件名。
- **密钥轮换**：新公钥随应用发布内置（`configs/plugin_trust.pub.pem` +
  `registry/keys/` 副本同步），旧签名仍可验证。
- **灾难恢复**：私钥 Shamir Secret Sharing 分片（N-of-M 恢复）。

## 6. 插件开发到上架全流程（目标态）

```
开发者（Plugin CDK / 本地身份）
  → 创建即签名（creator.sig + creator.identity）        [✅ 已落地: sign_plugin.py creator-sign]
  → 向 registry 提交 PR（plugin.yaml + 代码 + listing）   [✅ 已落地: git-as-registry + CI]
  → CI 自动校验（格式 + SHA256 + 创作者签名 + 权限）      [✅ 已落地: generate_catalog.py --check]
  → 社区审核员 Review
  → 维护者审核（代码安全 + 权限合理 + 功能正确）
  → 合并 PR
  → 维护者离线签名（plugin.py.sig）                     [✅ 已落地: sign_plugin.py sign]
  → 插件包上传 CDN / 对象存储                             [⏳ market-server 阶段]
  → 用户下载 → 验证 plugin.py.sig → 自动信任             [✅ 已落地: 三层信任加载链]
```

## 7. 触发市场服务器建设的信号

- 插件数 > 30 且出现非本人维护者参与的社区贡献；
- 需要：审核徽章、安装统计、多市场源、自动撤回公告分发；
- 有志愿者愿意运营（AGPL 服务器不是免费的）。

在此之前：registry 独立仓库 + GitHub 原生 PR 审核 + 现有 CI 门禁足以支撑
小规模生态；**不要提前建设服务器**。

---

## 已落地清单（对照 Helios 需求）

| Helios 需求 | 落地位置 |
|-------------|----------|
| 三库架构/迁移 | `registry/` 自包含（复制即拆库），market-server 本文档预留 |
| 用户名后缀 | `generate_catalog.py::_check_display_name_suffixes` + `registry/authors/` |
| 密钥安全（OS 密钥库） | `identity.py`（密码加密入 SecretsStore/keyring）+ `SecretsStore` env 支持 |
| 私钥四层保证 | 存储隔离（OS 密钥库）✅ / 允许列表打包（`scan_plugin.py --manifest`）✅ / 发布前扫描（五项）✅ / 导出接口无私钥路径（`export_identity()`）✅ |
| 开发者文件预留 | `registry/` 内 LICENSE/CONTRIBUTING/CODE_OF_CONDUCT/SECURITY/CHANGELOG/.github/ |
| 维护者签名发布 | `sign_plugin.py sign` + 透明日志 |
| 身份系统 | `identity.py` + `tools/identity.py` + GUI 身份对话框 |
| 创建即签名 | `sign_plugin.py creator-sign` |
| 三层信任模型 | `trust.py` + `plugins.py` 加载链接入 + 信任列表 |
| 双签名体系 | Ed25519（创作者+维护者）✅；cosign 不适配本地分发（不实施） |
| 沙箱分级 | 子进程沙箱 ✅；WASM 不实施（纯 Python）；未签名在配置信任根后拒绝 |
| 市场安全扫描 | `registry/tools/scan_plugin.py` 五项扫描 + 签名前自动执行 |
| 插件/模板/分块签名 | 插件 ✅ + 模板 ✅（同一签名/信任/分发机制）；分块模板（blocks/ 端口体系）依赖工作流画布，远期预留 |
| 签名透明日志 | `sign_plugin.py` → `signing_transparency.jsonl` |

## 模板市场（已落地）

- 市场源：`registry/templates/<id>/template.yaml`（`template:` 块含
  `publisher`/`author_fingerprint` 市场字段）
- 索引：`catalog.json` `templates` 数组（生成器聚合，CI 强制一致性 + 签名）
- 签名：`sign_plugin.py creator-sign/sign --file template.yaml`
- 安装：`tools/market.py templates install` → `templates_installed/<id>/` →
  `TemplateCatalog` 用户目录自动发现
- GUI：市场面板「插件 / 模板」双页

## 分块模板（远期预留）

Helios 的 block.json（端口 + 子 DAG + 3 层嵌套组合）依赖工作流画布能力。
OmniCrawler 现有 `recipes/` 提供静态组合；待引入可视化画布（DAG）后，
分块模板市场（`registry/blocks/` + 端口声明）复用同一签名/信任/分发机制即可。
