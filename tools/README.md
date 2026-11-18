# `tools/` 工程与验证工具导航

> 适用版本：**1.0.2** · 清单真源：[`gate_registry.py`](gate_registry.py) · 统一执行入口：[`self_verify.py`](self_verify.py)

本目录按职责划分为语义子包，顶层仅保留统一自证入口、门禁注册表、子进程编码工具及基线配置文件。

## 顶层入口与共享基线

| 路径 | 职责 |
|---|---|
| `self_verify.py` | 本地与 CI 共用的门禁聚合自证入口（支持 `--set static` 全量及 `--incremental` 增量快速自证） |
| `gate_registry.py` | 质量门禁唯一真源清单（与 `.github/workflows/` 双向一致性由单测强制约束） |
| `child_env.py` | 跨平台 UTF-8 子进程环境构造器 |
| `*-baseline.json` / `*-budget.json` / `anchor_digest.json` | 架构导入环、主窗口规模、GUI 约定、lint 豁免与锚段摘要单调基线 |

## 语义子目录

| 子目录 | 职责域 |
|---|---|
| `gates/` | 静态架构、SDK 契约、GUI 规范、文档一致性、发布完整性与网络边界等 `check_*.py` 守卫 |
| `builders/` | wheel、源码包、增量更新包、更新清单与运行时清单构建脚本 |
| `releasing/` | 版本演进、SBOM、校验和、溯源证明与发布收口工具 |
| `portable/` | 便携归档冒烟、覆盖升级冒烟与内置虚拟环境路径自愈（`rebase_venv.py`） |
| `signing/` | Ed25519 身份指纹、插件/组件/更新清单与市场批量签名工具 |
| `i18n/` | 国际化词条提取、`.po`/`.mo` 编译与覆盖率统计真源 |
| `benchmarks/` | 核心管线吞吐、自适应策略臂与文档解析质量基准工具 |
| `market/` | 市场快照同步、检出与本地市场辅助入口 |
| `qa/` | 覆盖率执行包装、离线验收、公开复测与本地走查演示环境 |
