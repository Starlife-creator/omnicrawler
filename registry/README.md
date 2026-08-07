# OmniCrawler 插件生态目录（Plugin Registry）

本目录是 OmniCrawler **策展式插件市场**的目录源（catalog）。当前托管在**主仓库**，
但设计上做到**可一键迁移到独立仓库**进行双仓管理（见下方「迁移到独立仓库」）。

> 设计原则：**所有文件路径都是相对于本目录（catalog 基址）的相对路径**。因此迁移
> 整个 `registry/` 子树到任何位置、任何仓库、任何静态 HTTP 服务后，只要把应用配置里的
> `plugins.catalog_url` 改成新基址，目录内部无需任何改动。

---

## 目录结构

```
registry/
├── catalog.json                 # 索引：所有已审核插件的机器可读清单
├── CATALOG_SCHEMA.md            # catalog.json 字段说明
├── README.md                    # 本文件
└── plugins/
    └── <plugin_id>/             # 每个插件一个目录，id 用小写字母/数字/下划线/短横线
        ├── plugin.py            # 插件代码（必须含 def register(registry)）
        ├── plugin.py.sig        # 与 plugin.py 同名的 detached ed25519 签名
        └── listing.md           # 强制功能说明（人类可读）
```

## 信任模型

- **单信任根 ed25519**：签名用持有者冷存储的私钥生成，验签用随包分发的公钥
  `configs/plugin_trust.pub.pem`。
- 应用加载插件前会 fail-closed 验签；验签失败直接拒载。
- 贡献者无法自签——提交后由持有私钥的发布者审核并签名，签名即背书。

## 提交一个新插件（贡献流程）

1. 在 `plugins/` 下新建 `<plugin_id>/` 目录。
2. 放入 `plugin.py`（含 `def register(registry)`）与 **强制的** `listing.md`
   （说明：做什么、适用场景、权限、兼容、作者、版本、许可）。
3. 通过插件契约测试（`tests/unit/plugin/`）。
4. 提交 PR；维护者审核 `listing.md` 与代码。
5. 审核通过后，由持有冷私钥的发布者在**冷机器**上签名：
   `python tools/sign_plugin.py sign plugins/<plugin_id>/plugin.py`
    （私钥位于维护者冷存储介质，绝不入库）。
6. 把插件条目加入 `catalog.json`（相对路径），更新 `generated_at`，合并。

> 后续 CI 门（待接入）：PR 若新增/更新 `plugins/<id>/`，必须附带 `listing.md`，
> 且对应 `plugin.py.sig` 必须能通过对信任根的有效验签，否则阻断合并。

## 迁移到独立仓库（双仓管理）

当生态变大、需要把目录与代码仓库分开管理时：

1. 新建独立仓库（如 `omnicrawler-plugins`）。
2. 将整个 `registry/` 子树（含 `catalog.json`、`CATALOG_SCHEMA.md`、所有 `plugins/*`）
   复制到新仓库根目录——由于内部全部是相对路径，复制即可，无需改写。
3. 在 OmniCrawler 应用的配置中把单一配置项指向新基址：
   ```yaml
   plugins:
     catalog_url: "https://raw.githubusercontent.com/Starlife-creator/omnicrawler-plugins/main/registry"
   ```
   （离线/便携构建则把 `registry/` 打包进应用，并将 `catalog_url` 改为内置快照目录
   `bundled_catalog_dir`。）
4. 完成。应用端无需改动任何加载/验签逻辑——信任根公钥 `configs/plugin_trust.pub.pem`
   不变，签名依然有效。

> 之所以"简单"，是因为：① 目录自包含、路径全相对；② 应用只认一个
> `catalog_url` 配置；③ 签名信任根跨仓库复用。三者共同保证迁移是"拷贝 + 改一个值"。
