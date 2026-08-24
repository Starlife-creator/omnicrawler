# 插件作者指南（Phase 3，第 57/75/76 轮）

本文档覆盖**契约 2 插件**从零到发布的完整旅程。作者本地验证 = CI 验证（F1"本地绿 = CI 绿"，
同一批测试、任一执行位置结果等价）。

## 0. 前置：受支持环境

| 平台 | 版本 | 沙箱后端 |
|---|---|---|
| Windows | Win10 22H2+ / Win11 | AppContainer（探测失败 → fail-closed + `--report` 回传） |
| Linux | 内核 ≥5.13 主流发行版 | unshare + seccomp + Landlock |

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

## 4. 签名与提交

- 签名是**发布动作**：维护者冷私钥离线持有（`tools/sign_catalog.py` 语义），CI 不持私钥。
- 提交走市场仓 PR → 四门禁 CI（门 1/2/3 + 凭据扫描）→ 合并 → tag 门禁生成 sha256 固化 +
  catalog 重签 + 门 4 变更规则。
- 发布后客户端下载校验：验签 → sha256 比对 → 吊销检查（G1/G2）。

## 5. 常见问题

- **我的环境不支持沙箱？** 运行 `omnicrawler plugins audit --report`，把报告粘贴至
  GitHub Issue（第 68 轮回传通道）；沙箱用例委托 CI 矩阵代跑（第 75 轮）。
- **契约 1 插件怎么办？** 市场侧 0.10 起要求契约 2；用 `scaffold-contract2` 新建契约 2 工程，
  业务逻辑按 SDK 指引迁移。迁移完成前仅本地显式信任 + 豁免表可申请 in_process（T3 最严格档）。
- **权限变化？** 权限变化必须重新获得用户批准（静态审批面），不得运行期静默扩大。
- **数据外传？** 插件可访问的数据 = 可外传的数据（威胁模型诚实边界）；`egress_policy: block`
  企业档阻断 records.read → network.fetch 共现。低频外传当前不可防，审计留痕供检视。
