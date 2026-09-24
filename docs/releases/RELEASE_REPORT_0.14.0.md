# OmniCrawler 0.14.0 发布报告

> 发布日期：2026-09-24；配置协议：v5；公共 API：兼容（新增 `view.richtext` 能力，纯扩展）。

## 发布结论

0.14.0 是一次**市场生态版本**：把 v0.13.1 之后的三条主线一次交付——
**内置模板治理与市场迁出**（模板库 78→41，市场接管站点适配器分发）、
**§十 P2 三个可选入口切片**（受限长文本组件 + 两个官方 `view` 插件上线）、
**§十一 前端/登录会话 8 项代码侧收口**（U/V/Q 三线）；另有《审查记录》§2.4
三条低/中遗留的全部闭环与 4 处 CI 漏网修复。

任务模型、工作区格式、导出格式**均未变化**；配置协议仍为 v5。
**升级注意**：`min_core_version >= 0.14.0` 的市场插件在更早版本上会**加载阶段明确拒载**
（不会半装）——本版起核心具备 `view.richtext` 能力，两个官方入口插件可正常安装。

## 已交付改进

### 一、内置模板治理与市场迁出（2026-09-23 ~ 24）

- **模板库 78 → 41 套**：退役 20 个 legacy 平面模板；跨入口模板发现（CLI 与 GUI 同源）；
  站点适配器（sites 4 + cms 6 + social 5 共 15 个）**只留市场分发**（内核副本删除，
  市场模板 3 → **15**），`b2` 域名映射 21 处改指 `*/market/*` id。
- 12 个站点适配器模板经**创作者签名 + 维护者整包签名**发布 1.0.0
  （`validate_submission` 20/20、`generate_catalog --check` 绿）。
- 投稿链路 **LF 规范化**（listing/manifest 字节跨平台一致，杜绝 CRLF 哈希漂移）；
  `constraints/market-ref.txt` pin 随每次 `sync_snapshot` 成对推进。
- **跨入口模板发现**（CLI 与 GUI 同一 catalog 真源）；**隐身分级**接线
  （`browser.stealth_level`）。

### 二、§十 P2 可选入口三片（2026-09-24）

- **P2.1 受限长文本组件**：新能力 `view.richtext`（v1）；`rich_text` 组件——段类型
  白名单（heading/paragraph/bullet/link）、link 仅 http(s)、段数 ≤64、总字符 ≤4096；
  宿主**纯文本渲染、绝不解释 HTML**；外链点击经确认后才在系统浏览器打开；
  **拒载时序实测**：能力校验先于插件子进程构造（老 core 在执行插件代码前拒载）。
- **P2.2 `issue-wishlist` 1.0.0（需求清单）**：GitHub 开放 Issues 只读浏览
  （`network:scoped`，domains 限定 `api.github.com`，egress 策略 + 日配额约束，
  零凭据；安装即用不自动联网；PR 自动过滤）。
- **P2.3 `community-guide` 1.0.0（社区引导）**：外链降级版（仓库未启用 Discussions，
  按 2026-09-20 拍板）——**零网络**、纯静态官方入口导航（Discussions / Issues /
  贡献指南 / 行为准则）。

### 三、§十一 前端优化与登录会话（8 项代码侧，2026-09-23）

- **U1–U3**：headed Playwright 登录窗口（与爬取同引擎同指纹）、会话按 domain 归还的
  语义转换（43 项单测 + 10 条反向断言，绝不整 jar 倒灌）、独立导航页（默认开、可关、
  可延时；到点先保存再关闭）。
- **U4（代码侧）**：401/302 → 登录联动提示；**实机验证移出批序**（并入统一实机验收）。
- **U5 会话加密**：storage_state 以 AES-GCM 信封落盘（复用既有密码学，信封损坏绝不
  回退明文）；用户指南已显式声明。
- **V1**：QtCharts 替换进度条式图表（实测零新增依赖，PySide6 元包已连带 Addons）；
  **V2** 设计系统深化；**Q1** QML 试点（拍板：保留代码、不打包）。

### 四、治理与遗留闭环（2026-09-24）

- **TOFU 补钉**：tessdata_fast 三语言包钉 commit + SHA-256 + fail-closed
  （`RequireKnownHash`；反向断言实测红）；3 个手动 GitHub 资产此前已钉。
- **yaml_editor 同步校验覆盖全部段**（不再只重造 project/source 两段）；
  **`REQUIRED_TOP_KEYS` 强制补生效**（原定义但零调用点——缺必需段此前静默通过）。
- **change_monitor 改用 settings 公共接口**：全仓外部私有调用点清零。

### 五、CI 与流程修复（4 处漏网，全部查明根因）

- GUI 冒烟第二份必需模板集（与 catalog 守卫重复定义）；
- `market-ref` pin 未随 `sync_snapshot` 推进（**两者是同一收口动作的两半**）；
- 投稿 listing 的 CRLF 字节漂移（投稿链路四处 LF 规范化）；
- 守卫改写时误吞既有断言两次（当场发现补回，零净损失）。

## 已知限制（如实声明）

- `issue-wishlist` 需要网络与配额：离线/限流时保留上次数据并提示，不阻塞主流程；
- `community-guide` 为外链引导（Discussions API 拉取待仓库启用后另行拍板）；
- 未启用 Discussions ⇒ 社区发言一律在系统浏览器完成，应用不持凭据。

## 升级与兼容

- 配置协议 v5 不变；公共 API 兼容（`view.richtext` 为新增能力，纯扩展）；
- 市场插件 `min_core_version` 检查在加载阶段 fail-closed；
- 从 0.13.x 升级：模板市场内容以市场仓为准（内核已删站点适配器副本，
  已安装用户不受影响，新装走市场）。
