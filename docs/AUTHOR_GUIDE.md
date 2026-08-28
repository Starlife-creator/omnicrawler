# 插件作者指南

本文档覆盖**契约 2 插件**从零到发布的完整旅程。作者本地验证 = CI 验证（F1"本地绿 = CI 绿"，
同一批测试、任一执行位置结果等价）。

## 0. 前置：受支持环境

| 平台 | 版本 | 实际隔离机制（v0.9.1） |
|---|---|---|
| Windows | Win10 22H2+ / Win11 | 子进程边界 + `-I -S` 导入隔离 + env 白名单（冻结形态用伴生宿主 exe） |
| Linux | 内核 ≥5.13 主流发行版 | 同上 + `resource` rlimit 限额 |

> 口径说明（FINAL-S1）：OS 级 confinement（AppContainer / unshare+seccomp+Landlock）
> 属**远期蓝图，当前未接线**——`plugin_os_sandbox.probe_os_sandbox` 仅产出环境诊断
> 报告，不参与 spawn 拒载裁决。插件能力收口由 broker 能力令牌 + 静态审批实现，
> 不依赖 OS 沙箱；冻结宿主 exe 缺失时 fail-closed 拒载仍然生效。

非受支持环境作者：**门禁/AST/逻辑用例本地照跑 + 沙箱用例 fork + PR 由 CI 矩阵代跑**
（第 75 轮 CI 委托路径）——收窄不成为参与门槛。

## 1. 生成脚手架

```bash
omnicrawler plugins scaffold-contract2 --plugin-id my_plugin --display-name "我的插件"
```

生成：`plugin.py`（PLUGIN_METADATA + handle 骨架）+ `plugin.yaml`（双通道字段对齐）+
`tests/test_contract.py`（继承 Contract2Suite）+ `listing.md`。

## 2. 实现业务逻辑

- 在 `handle(operation, payload)` 内按操作分派；`source.seed` 返回 `{"requests": [...]}`。
- 能力调用经 `omnicrawler_sdk.call(...)`；**不要 import omnicrawler**（沙箱内不可用，门 1）。
- 需要网络 → `network:scoped` + `domains`；需要读文件 → `files:read` + `input_files` 白名单；
  需要密钥 → 优先 `auth` 注入（零暴露），`secrets.get` 是显式例外。

## 3. 本地验证（发布前必做）

```bash
omnicrawler plugins audit --local .        # 门 1/门 2/门 3 + 契约一致性 + 沙箱探测
pytest -m plugin_contract                  # F1 公共契约套件（沙箱隔离/协议/越权拦截）
```

- `PLUGIN_METADATA` 与 `plugin.yaml` **逐字段一致**（门 3）；`dependencies` 与实测导入图
  **双向互证**（声明未导入 / 导入未声明均拒）。
- `license` 在 SPDX 白名单内（门 2）。

## 4. 完成并签名：此时已经可以私下分享

创作者完成插件后，先用自己的本地身份签署**整个文件夹**，而不是等到决定上市场才签名。
GUI 的“完成并签名”会隐藏 manifest 和 ed25519 操作，但会在执行前显示将公开的文件、权限、
域名和身份指纹。产物包括：

- `package.manifest.json`；
- `package.manifest.creator.sig`；
- `creator.identity`；
- manifest 覆盖的 `plugin.py`、`plugin.yaml`、`listing.md` 等载荷。

这一目录有两个平等的后续选择：

1. 直接把文件夹发给他人。接收方在“第三方分享”页导入，应用先验整包签名，再显示来源、
   指纹、权限和域名。用户确认后默认只信任当前包，不自动信任作者未来所有插件。
2. 投稿市场。应用把同一份创作者签名字节放入 `submissions/` 并创建 Draft PR；不重新打包，
   市场也不能改写创作者签名覆盖的内容。

私下分享代表“可验证地来自某把创作者密钥”，不代表经过市场审核。

## 5. 投稿与正式发布

- 投稿前必须填写 `listing.md`，并明确确认 DCO；应用才会自动创建带 `Signed-off-by` 的提交。
- 外部 PR CI 只做签名、哈希、路径、AST/YAML、凭据泄漏和 DCO 静态检查，绝不执行投稿插件。
- 用户名不是归属凭据。本地用户名可重复；正式发布时市场依据指纹分配稳定唯一 handle，
  同名后来者自动得到 `-01`、`-02` 后缀。
- 维护者人工核对 manifest 完整哈希、权限、域名、依赖和许可后，用冷私钥复签**同一份
  manifest**。只有通过维护者整包签名并进入已签名 catalog 的版本才属于市场发布态。
- 客户端先验证 `catalog.json.sig` 并防旧目录回放，再验证创作者和维护者整包签名、精确文件
  集合与哈希，任一步失败都拒绝安装。
- 更新必须由同一创作者指纹签名，SemVer 严格递增；新版独立保存，不覆盖旧版签名字节。

## 6. 模板是否同步

同步。模板虽然是声明式 YAML，仍采用同样的“完成即可私下分享 → 可选投稿 → 维护者复签 →
目录签名”流程。模板还会静态检查 `template.id/version`、域名、固定 seed、`secret://` 凭据引用
和数据来源条款。插件与模板不会形成两套身份或发布制度。

## 7. 常见问题

- **我的环境不支持沙箱？** 运行 `omnicrawler plugins audit --report`，把报告粘贴至
  GitHub Issue（第 68 轮回传通道）；沙箱用例委托 CI 矩阵代跑（第 75 轮）。
- **契约 1 插件怎么办？** 市场侧 0.10 起要求契约 2；用 `scaffold-contract2` 新建契约 2 工程，
  业务逻辑按 SDK 指引迁移。迁移完成前仅本地显式信任 + 豁免表可申请 in_process（T3 最严格档）。
- **权限变化？** 权限变化必须重新获得用户批准（静态审批面），不得运行期静默扩大。
- **数据外传？** 插件可访问的数据 = 可外传的数据（威胁模型诚实边界）；`egress_policy: block`
  企业档阻断 records.read → network.fetch 共现。低频外传当前不可防，审计留痕供检视。
