# 2026-11-01 正式验收前收口记录

## 范围与终态

- 分支：`preacceptance-closure-20261101`；工作树：`.agents/worktrees/preacceptance-closure-20261101`。
- 本批从提交 `0ea02e3` 至 `412d518`；这是源码环境预验，不是新版本发布、便携包验收或人工验收完成声明。
- 本机已获授权使用完整依赖进行 GUI/浏览器/Full 验收；本轮只记录实际执行过的项目，未执行项不以自动测试替代。

## 本批实际收口

- `0ea02e3`：登录会话视图测试使用独立临时工作区，避免共享 `work/` 的 cookie 污染。Qt offscreen 全量 GUI 回归曾得到 **495 passed / 1 skipped**。
- `5995740`：真实测量 200 runs / 100000 `run_state_events` 的三处 GUI 查询。`run_compare_rows_30`、`reprocess_review_load`、`reprocess_review_resolve_reject` 中位分别为 **0.115 / 0.032 / 0.255 ms**，均低于 50 ms 异步迁移阈值。补充 `list_runs_500` 中位 0.228 ms、最大 0.623 ms；`run_events` 中位 1.948 ms、最大 2.848 ms。证据：`gui-state-loading.json`。这些只是本机 SQLite 测量，不是跨平台性能承诺。
- `2d0b6d6`：插件 `state.migrate` 在源 schema 中排除敏感键，并报告跳过项，不把凭据复制进新 schema 的可见 state。
- `3de2b4d`：PDF 处理依赖（解析器、OCR 组件与 Tesseract 可执行文件）变化时失效并重建可重建输出；人工复核结果受保护；OCR 失败不推进成功指纹。PDF 单元、集成和 CLI 转发回归为 **219 passed / 7 skipped**。临时移除刷新调用后四个依赖刷新用例均失败，恢复后通过。
- `112b162`：收口上述实现的静态类型；全量 mypy **513 source files** 无错误。
- `412d518`：外部工作区治理测试不再向 Windows 根目录 `/tmp` 写文件，使用 `tmp_path.parent` 的真实外部临时路径。

## 真实入口与插件证据

### 固定样本 CLI + Markdown

- 启动器：`python tools/walkthrough_env.py --workspace E:\tool\biancheng\omnicrawler\.dsh-temp\walkthrough-preacceptance --port 18080`。
- 项目根：`E:\tool\biancheng\omnicrawler\.dsh-temp\walkthrough-preacceptance\project`；数据根：该工作区的 `data`；配置：`project/task.yaml`。
- 运行 `f074b3bfbda14479bf4f96d3e0f58eff`：两个显式种子（`/` 与 `/list?page=2`）、2 页、8 条、0 errors、无待处理 frontier；标题、价格、详情链接逐字段对照演示站点真值一致。
- 通过 `MarkdownExporter.export_results` 实际生成 `project/work/output/records.md`；同时生成 CSV、JSONL、XLSX 和质量报告。
- 这不是 GUI 的人工点击验收，也没有据此勾选 PDF 附件/OCR 人工复核。

### 固定市场插件

- `constraints/market-ref.txt` 的精确提交：`f33fcac397e050d02e7fb2ae80cd93504fd19697`。
- 实际检出到 `.agents/worktrees/OmniCrawler-market`；`tests/unit/plugin/test_paper_scenario_boundary.py` **3 passed**，验证不适配的论文插件在执行前被拒绝。
- 这不等于完成市场 GUI 的安装、更新、卸载体验，也不改变既定的不融合 `academic-paper-downloader` 当前实现、不融合 stealth-browser-mcp 的决定。

## 全量回归与保留环境失败

最终全量命令使用仓外 `E:\tool\biancheng\omnicrawler\.dsh-temp\unit-final-verified` 和 `PYTHONUTF8=1`，避免临时配置上溯仓库 `pyproject.toml` 造成共享 `work/`，并修复子进程默认 GBK 解码问题。

上一轮相同隔离条件下为 **7 failed / 5177 passed / 87 skipped**。随后已消除：外部工作区路径 1 项、市场快照缺失 1 项。剩余 **5 项**均在本机 Bash/MSYS 边界，最终结果以本轮命令输出为准：

- 两个 shell 脚本用例：`bash -n` 无法运行。WSL 垫片报 `0x80070422`，Git Bash/MSYS 报 `NtCreateDirectoryObject(\BaseNamedObjects\msys-2.0...): 0xC0000022`。
- 三个本地市场检出用例：Python 启动的 Git 子进程在同一 MSYS 边界失败，`git fetch` 返回 128。直接 PowerShell 的本地 fetch 可成功，但不能替代工具子进程的成功证明。尝试 `remote.origin.uploadpack` 与 `core.gitProxy` 均未消除该子进程失败，已还原，未把失败实现提交。

因此：

- `static-preacceptance-final.json`：**22 passed / 1 failed / 0 skipped**，唯一失败为 `check_shell_scripts`，其余 compileall、ruff、mypy、架构、网络边界、发布完整性、GUI 线程、市场内容等均通过。
- 不把失败归类为已证实的 shell 脚本语法错误；真实 POSIX `bash -n` 仍须由可用环境/CI 执行。
- 不把本地 Git/MSYS 失败掩盖为测试跳过或错误绿；可用 Git/MSYS 环境仍需重跑这三个用例。
- 非仓内的大型测量数据库、运行时 lock、`imported-work/` 和 `.config_history/` 不属于交付，不纳入提交。

## 正式验收前仍需人工或外部环境

- 完整 A–G 真实桌面点击、视觉、键盘/无障碍与 PDF/OCR 人工复核；offscreen 与自动测试不能代替。
- 真实站点账号、验证码/双因素、供应商实际计费与业务 PDF 真值。
- 干净机器断网安装、正式便携产物内的关键旅程、三平台实机与正式签名/组件归属。
- 插件 OS 沙箱仍为 Roadmap；远端 Worker 仍 Reserved。不得把 Python 子进程隔离称为 AppContainer、Landlock、seccomp、Seatbelt 或 Job Object。
- PDF/OCR 的远端同名模型、Paddle 隐式缓存和 Tesseract 语言数据仍不是完整内容身份；业务真值校准也仍需真实样本。
- 当前变更分支尚需维护者合入/推送后，在最终提交上核验 quality / end-to-end CI；历史主分支绿色不继承为当前提交通过。
