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

---

## 追加：git-as-registry 升级（2026-08-09）

### 背景

原设计 `catalog.json` 是手写的唯一索引：新增/更新插件时需人工同步 JSON 条目，
且无作者身份记录（发布者仅是字符串），社区贡献的门槛与出错面都偏高。

### 决策

把 `registry/` 升级为 **git-as-registry**（目录结构即索引）模式，应用端**零改动**：

1. **`plugins/<id>/plugin.yaml` 成为唯一元数据源**：每个插件一个 YAML 清单，
   字段与 catalog 条目一一对应，另加 `author_fingerprint`。
2. **`authors/<username>.yaml` 记录发布者身份**：`username` / `pubkey_ref` /
   `fingerprint`（公钥 SHA-256 前 16 字节 hex，生态绝对唯一标识）。
3. **`catalog.json` 变为派生物**：由 `tools/generate_catalog.py` 聚合生成并随仓库提交；
   应用端（`market_client` / GUI 市场面板 / `tools/market.py`）继续只读它，schema 不变。
4. **CI 门禁（`.github/workflows/registry.yml`）**：PR 修改 `registry/` 时执行
   `generate_catalog.py --check`——校验清单合法、与生成物一致、作者指纹匹配、
   签名通过信任根验签，否则阻断合并。

### 影响

- 正向：插件元数据与代码同目录可审；作者身份可验证（指纹 ↔ 公钥强校验）；
  新增插件的正确流程 = 写 YAML + 签名 + 跑生成器，无需手改 JSON；迁移到独立仓库
  的"拷贝 + 改 `catalog_url`"承诺保持不变。
- 约束：`catalog.json` 禁止手改（CI 会抓漂移）；发布者首次入场需先提交
  `authors/` 记录。

## 追加：registry/ 自包含升级（2026-08-09）

### 背景

上一版 `registry/` 仍有一处跨仓库引用：`authors/*.yaml` 的 `pubkey_ref` 指向主仓库
`configs/plugin_trust.pub.pem`，生成器依赖应用包 `omnicrawl.plugins.signing` 验签。
拆库时需要复制公钥并改引用，生成器也无法在独立仓库运行——"拷贝 + 改一个值"并不成立。

### 决策

让 `registry/` **完全自包含**，拆库退化为纯粹的"拷贝 + 改 `catalog_url`"：

1. **信任根公钥副本进入 `registry/keys/plugin_trust.pub.pem`**；`authors/*.yaml`
   的 `pubkey_ref` 改为相对 `registry/` 的路径（`../keys/plugin_trust.pub.pem`）。
   公钥是公开文件，主仓库 `configs/` 副本保留（应用端信任根配置不变）。
2. **生成器迁入 `registry/tools/generate_catalog.py` 并自包含**：ed25519 验签内联
   实现（仅依赖 PyYAML + cryptography），不再 import 应用包；信任根查找链
   `--trust` > `registry/keys/` > 主仓库 `configs/`（回退，拆库后第二项自然失效）。
   `catalog.json` 顶层 `trust_public_key_ref` 更新为 `keys/plugin_trust.pub.pem`。
3. **CI 演练拆库**：`registry.yml` 增加"复制 `registry/` 到临时目录 → 独立校验 +
   市场 CLI 消费"步骤，任何时刻保证复制即成立。
4. 测试新增 `test_standalone_copy_passes_check` 本地演练同一场景。

### 影响

- 正向：拆库 = 复制 `registry/` + 复制 CI 文件 + 改 `catalog_url`，无其他改动；
  独立仓库无需应用包即可运行校验工具。
- 约束：唯一双维护点是公钥副本（主仓库 `configs/` ↔ 生态 `keys/`），轮换时两边同步。

## 追加：身份系统与三层信任模型（阶段 1，2026-08-09）

### 背景

照搬 Helios 市场生态设计（用户需求：完整身份系统 + 维护者签名 + 三层信任），
将插件信任链从"单一信任根"演进为"创作者签名 + 维护者签名"双签名体系。

### 决策

1. **本地身份系统**（`src/omnicrawl/plugins/identity.py`）：首次使用创建本地身份
   （用户名 + 密码，纯本地），自动生成 Ed25519 密钥对；私钥经密码派生密钥
   （PBKDF2-HMAC-SHA256，60 万次迭代）二次加密后存入 OS keyring 保护的
   SecretsStore——私钥绝不落盘明文、绝不入库（对齐 Helios §13.7/§13.10）。
   公开身份 `CreatorIdentity`（username/public_key/fingerprint）可随插件分发。
2. **创建即签名**：`tools/sign_plugin.py creator-sign` 用本地身份生成
   `creator.sig` + `creator.identity`；`sign` 由维护者在冷机器生成
   `plugin.py.sig`（市场分发携带）。
3. **三层信任模型**（`src/omnicrawl/plugins/trust.py`）：
   - 层级 1：`plugin.py.sig` 通过信任根验签 → 自动信任；（旧版 `maintainer.sig` 文件名已弃用，验证器不再兼容）
   - 层级 2：`creator.sig` + `creator.identity` 有效且指纹在本地信任列表
     （`~/.omnicrawl/trusted_users.json`，纯本地决策）→ 信任；
   - 层级 2b：创作者签名有效但未信任 → 拒绝加载并弹出信任确认弹窗（GUI 已通过 QMessageBox 实现，确认后写入 `trusted_users.json`）；
   - 层级 3：无有效签名 → 拒绝（配置信任根时）；未配置信任根保留开发者模式警告加载。
4. **SecretsStore 环境隔离**：新增 `OMNICRAWL_SECRET_STORE_PATH` /
   `OMNICRAWL_KEYRING_DISABLE` / `OMNICRAWL_MASTER_PASSWORD` 支持（便携/测试）。
5. 信任列表管理 CLI：`tools/identity.py trust add|revoke|list`。

### 影响

- 正向：P2P 插件分发成为可能（创作者签名 + 信任提示）；未签名插件在配置信任根
  时被拒绝（对齐 Helios 层级 3）；单文件形态插件（dev 工具）保留旧验签路径。
- 差异（有意保留）：Helios 对未签名插件一律拒绝；OmniCrawler 在**未配置信任根**
  时保留开发者模式（显式警告加载），配置信任根后即为拒绝。
- 待办（阶段 2）：GUI 首次启动身份设置、P2P 信任提示弹窗、市场徽章显示。

## 追加：模板纳入市场生态（2026-08-09）

### 决策

模板（声明式配置）与插件共享签名、信任与分发机制（对齐 Helios 三层体系）：

1. **`registry/templates/<id>/template.yaml` 成为市场模板源**：`template:` 块新增
   `publisher` / `author_fingerprint` 市场字段（内置模板不需要）；模板 ID 允许
   层级命名（`demo/template`），插件 ID 保持严格。
2. **`catalog.json` 新增 `templates` 数组**（schema_version 保持 1，向后兼容）；
   生成器扫描 `templates/*/template.yaml` 并校验：project/source 存在、市场字段
   必填、作者指纹匹配、文件存在、签名验签（信任根）。
3. **签名复用**：`sign_plugin.py creator-sign/sign` 增加 `--file`
   参数（默认 `plugin.py`，模板传 `template.yaml`）。
4. **应用端**：`market_client.download_template_and_verify` /
   `verify_installed_template`（安装到 `templates_installed/<id>/`，模板库
   `user_dirs` 自动发现）；`tools/market.py templates list|info|install|verify`；
   GUI 市场面板改为「插件 / 模板」双页（QTabWidget）。
5. 分块模板（blocks/，端口体系）依赖工作流画布能力，暂不实现，见
   `docs/MARKET_ECOSYSTEM.md` 远期预留。

### 影响

- 正向：模板生态闭环（贡献 → 签名 → 市场 → 安装 → 模板库发现），P2P 分享模板
  与插件同机制。
- 约束：市场模板必须声明 `publisher`/`author_fingerprint`；模板签名由
  生成器/CI 强制（fail-closed）。
