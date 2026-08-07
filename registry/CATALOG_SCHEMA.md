# Catalog Schema（`catalog.json`）

`catalog.json` 是插件市场的索引文件。应用端的「市场面板」读取它，向用户展示可安装插件，
并据此下载、验签、安装。

## 顶层字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `schema_version` | int | catalog 格式版本，当前 `1` |
| `generated_at` | string (ISO8601) | 生成时间，便于缓存失效 |
| `publisher` | string | 目录发布者（生态 owner） |
| `trust_model` | string | 信任模型，当前固定 `single-root-ed25519` |
| `trust_public_key_ref` | string | 验签公钥引用（相对路径或 PEM/PEM 路径），与应用 `plugins.trust_public_key` 一致 |
| `plugins` | array | 已审核插件条目数组 |

## 插件条目字段（`plugins[]`）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | string | ✓ | 插件唯一 ID，正则 `^[a-z][a-z0-9_-]{1,63}$` |
| `name` | string | ✓ | 展示名 |
| `version` | string (semver) | ✓ | 插件版本 |
| `publisher` | string | ✓ | 发布者 |
| `category` | string | ✓ | 扩展点类别：`source` / `fetcher` / `processor` / `exporter` / `auth_provider` / `parser` / `extractor` / `transformer` / `hook` |
| `summary` | string | ✓ | 一句话功能摘要 |
| `description_file` | string | ✓ | **功能说明**文件相对路径（即 `listing.md`） |
| `plugin_file` | string | ✓ | 插件代码相对路径 |
| `signature_file` | string | ✓ | detached 签名相对路径（与 `plugin_file` 同名 + `.sig`） |
| `signature_algorithm` | string | ✓ | 当前固定 `ed25519` |
| `permissions` | array[string] | ✓ | 插件声明的权限列表（空数组表示无） |
| `compatible_core` | string | ✓ | 兼容的核心版本约束，如 `>=2.7.0` |
| `license` | string | | 许可协议（如 `MIT`） |
| `tags` | array[string] | | 标签，便于检索 |
| `updated_at` | string (date) | | 最近更新日期 |
| `homepage` | string (URL) | | 插件主页（可选） |

## 路径约定（迁移友好）

- `description_file` / `plugin_file` / `signature_file` 全部是**相对于 catalog 基址**的路径。
- catalog 基址由应用配置 `plugins.catalog_url` 决定（默认主仓库 raw 地址）。
- 因此移动整个 `registry/` 到新仓库/新服务后，只需改 `catalog_url`，条目内路径不变。

## 签名与验签

- 下载 `plugin_file` 与 `signature_file` 后，用信任根公钥验证 `signature_file` 是否覆盖 `plugin_file` 字节。
- 验签失败的应用端行为：fail-closed 拒载，并在市场面板标记该插件「不可信」。
- 撤销：生态注册表 `EcosystemRegistry.revoke(package_id, version, advisory)` 记录撤回；
  重新生成 catalog 时把被撤回条目移除或标记。
