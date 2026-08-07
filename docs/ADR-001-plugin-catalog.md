# ADR-001：插件市场 Catalog 托管与双仓就绪设计

- 状态：已采纳（Accepted）
- 日期：2026-08-07
- 相关：C9 插件离线 ed25519 签名（`src/omnicrawl/plugins/signing.py`）、`EcosystemRegistry`、插件加载门（`src/omnicrawl/plugins/plugins.py`）

## 背景 / Context

OmniCrawler 的愿景是**策展式插件生态**：有人提交插件 → 维护者审核 → 纳入生态 →
联网用户在 GUI 看到已审核插件 → 按需自选下载安装；每份提交必须附带功能说明（类 agent
专家/技能市场）。

插件框架已就绪（C9 签名、fail-closed 加载门、撤回表）。现在要决定 **catalog（插件目录源）
放在哪里**，并要求**后续能简单迁移到独立仓库做双仓管理**，不影响加载/验签逻辑。

## 决策 / Decision

1. **现阶段 catalog 托管在主仓库根目录的 `registry/`**（不新建仓库、不引 HTTP 服务）。
2. **全部 catalog 内部路径均为相对路径**，基址由应用单一配置项 `plugins.catalog_url`
   决定（默认 `https://raw.githubusercontent.com/Starlife-creator/omnicrawler/main/registry`）。
3. **复用既有信任根** `configs/plugin_trust.pub.pem` 验签，签名跨仓库有效。
4. **离线/便携就绪**：保留 `plugins.bundled_catalog_dir` 配置，便携包可把 `registry/`
   打包进应用并填此路径，使市场离线可用。
5. **每一份插件提交强制附带 `listing.md` 功能说明**（人类可读），机器可读元数据进 `catalog.json`。

## 设计要点

### 目录布局（自包含、可整体搬运）
```
registry/
├── catalog.json          # 索引（机器可读）
├── CATALOG_SCHEMA.md     # 字段说明
├── README.md             # 贡献流程 + 迁移到独立仓库指南
└── plugins/<plugin_id>/
    ├── plugin.py         # 插件代码（含 def register）
    ├── plugin.py.sig     # detached ed25519 签名（同名）
    └── listing.md        # 强制功能说明
```

### 单一可迁移配置（关键）
- `plugins.catalog_url`：catalog 基址。应用端只认这一个值。
- `plugins.bundled_catalog_dir`：离线快照目录（可选）。
- 新增 `AppConfig.plugin_catalog_url` / `plugin_bundled_catalog_dir` 属性供市场面板读取。

### 信任模型
- 单信任根 ed25519，公钥随包分发；加载前 fail-closed 验签。
- 贡献者无法自签，签名即背书。

## 迁移到独立仓库（双仓管理）步骤

1. 新建独立仓库（如 `omnicrawler-plugins`）。
2. 把整个 `registry/` 子树复制到新仓库根——因内部全为相对路径，**复制即可，无需改写**。
3. 应用配置改一项：
   ```yaml
   plugins:
     catalog_url: "https://raw.githubusercontent.com/Starlife-creator/omnicrawler-plugins/main/registry"
   ```
4. 完成。加载/验签逻辑零改动，信任根公钥不变，签名依然有效。

> 之所以"简单"，源于三点：① 目录自包含、路径全相对；② 应用只认一个 `catalog_url`；
> ③ 签名信任根跨仓库复用。迁移 = 拷贝 + 改一个值。

## 后果 / Consequences

- 正面：零新增基础设施；复用 git 审核流与 C9 签名；迁移成本极低；离线/在线双模就绪。
- 负面/待办：
  - 主仓库 `registry/` 与代码同源，PR 噪音略增（后续可借 CODEOWNERS 分流）。
  - 国内 GitHub raw 不稳 → 后续可把 `catalog_url` 指向镜像/自托管（同一配置项解决）。
  - GUI 市场面板（✅ 已实现 2026-08-08，见下一步②）；下载+验签安装流（CLI 已落地）、
    提交强制 `listing.md` 的 CI 门尚未实现（见下）。

## 下一步 / Next Steps

1. **CLI/SDK 取 catalog + 验签安装（✅ 已实现 2026-08-07）**：
   - `src/omnicrawl/plugins/market_client.py`：`fetch_catalog`（远程/本地 catalog 基址）、
     `download_and_verify`（下载 plugin+签名+listing，ed25519 验签 fail-closed，落盘
     `dest_root/<id>/`）、`verify_installed`、`fetch_resource`。仅标准库 urllib，零新依赖。
   - `tools/market.py` CLI：`list` / `info` / `install` / `verify`；`--catalog-url` 覆盖
     （指向镜像/新仓库即完成迁移），信任根默认 `configs/plugin_trust.pub.pem`。
   - 加载器增强：`load_local_plugins` 支持目录递归，使安装目录 `plugins_installed` 经
     `plugins.paths` 配置即可被加载。`.gitignore` 已忽略 `plugins_installed/`。
2. **GUI 市场面板（✅ 已实现 2026-08-08）**：`src/omnicrawl/gui/views/plugin_market.py`
   `PluginMarketView`；经 `main.py` 的 `NavIndex.PLUGIN_MARKET = 8` 接入主导航（`🧩 插件市场`）。
   - 联网时从 `catalog_url` 拉取目录（远程失败回退本地 `registry/`），列表展示已审核插件
     （名称/版本/状态徽标/标签），详情面板展示 `listing.md` + 元数据。
   - 安装/卸载/校验按钮全部经 `market_client` 的 ed25519 验签（fail-closed）；离线时仅展示
     已安装列表并禁用联网操作。
   - 测试：`tests/gui/test_plugin_market.py`（PyQt6 skip 守卫，5 例全绿）。
3. **CI 门（待做）**：PR 新增/更新 `plugins/<id>/` 必须带 `listing.md`，且 `plugin.py.sig` 必须
   通过信任根验签，否则阻断合并。
4. **撤回机制（待做）**：接入 `EcosystemRegistry.revoke`，重新生成 catalog 时移除被撤回条目。

## 已落地产物（本 ADR 同期）

- `registry/catalog.json` + `CATALOG_SCHEMA.md` + `README.md`
- `registry/plugins/example_news/`（plugin.py + plugin.py.sig + listing.md）——首个已签名条目
- `src/omnicrawl/core/config.py`：`plugins.catalog_url` / `bundled_catalog_dir` 及 `AppConfig` 属性
