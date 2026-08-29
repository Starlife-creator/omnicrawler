# OmniCrawler 贡献指南

感谢你对 OmniCrawler 项目的关注！本文档描述了参与开发需要遵循的规范和流程。

## 快速开始

```bash
# 克隆仓库
git clone <repo-url> && cd OmniCrawler

# 安装开发依赖
pip install -e ".[full,dev]"

# 安装 pre-commit hooks
pip install pre-commit && pre-commit install

# 运行测试
pytest

# 运行质量门禁
ruff check src/ && ruff format --check src/
mypy src/omnicrawler/ --exclude 'src/omnicrawler/(gui|pdfx|apps)/'
python -m compileall src/ -q
```

## 质量门禁（红线）

所有提交必须通过以下门禁，任何一项不通过即不可合入：

| 门禁 | 阈值 | 说明 |
|------|------|------|
| ruff | 0 violations | 代码风格 + 导入排序 |
| mypy | 0 errors | 类型检查（GUI/pdfx/apps 暂排除） |
| compileall | 0 errors | 语法编译检查 |
| 覆盖率 | >= 66% | 全源码覆盖率，并通过分组门禁（下一目标 70%，长期目标 80%） |
| pytest | 全部通过 | 1800+ 测试用例 |

pre-commit hooks 会在提交时自动运行 ruff 和 mypy，从源头防止退化。

## 架构原则（宪法）

以下 6 条原则不可违反，触及前必须先说明影响并征得确认：

1. **分层隔离** — 按"差异来源"分层（source/template → fetcher → parser/extractor → transformer → exporter）
2. **Pipeline 只编排** — 九阶段管线只编排不干活，具体能力下沉到组件
3. **统一入口** — 所有外部调用经 ApplicationService，返回 dict DTO
4. **配置先编译为 Task IR** — 不得新增"跳过 IR 直接跑"的捷径
5. **持久化走 Repository Port** — 上层不得直接 import sqlite3
6. **异常隔离** — 单 URL 失败不拖垮整轮 run

详见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

## 提交流程

1. **创建分支**：`feat/`、`fix/`、`refactor/`、`docs/`、`test/` 前缀
2. **编写代码**：遵循类型注解规范（Python 3.10+ 风格：`str | None` 而非 `Optional[str]`）
3. **补充测试**：新增功能必须带对应测试；可选/重型能力用 skip 优雅降级
4. **本地验证**：运行 `pre-commit run --all-files` 确保门禁全绿
5. **提交 PR**：PR 描述包含变更摘要、测试方式、是否触及架构原则
6. **代码审查**：至少 1 人 approve 后合入；架构变更需 2 人 approve

## PR 行为约束

- **PR <= 400 行**（目标占比 >80%），大型重构拆分为多个 PR
- **不跳过 hooks**（不使用 `--no-verify`），除非用户明确要求
- **不引入未使用导入**，不吞咽异常
- **日志用英文**，GUI 用户提示用中文，异常消息用英文

## 插件与模板贡献

插件和模板首先是作者拥有的文件夹。作者完成内容并签署整个目录后，既可以私下分享，
也可以选择投稿市场；上传不是生成可分享包的前置条件。

### 普通贡献者

1. 新插件使用契约 2 脚手架，入口为 `handle(operation, payload) -> dict`；不要为新插件使用
   `register(registry)` 旧契约。
2. 在本地工作目录完成 `plugin.py`、`plugin.yaml`、`listing.md` 和测试；模板完成
   `template.yaml`、`listing.md` 和必要的验证素材。
3. 运行本地审计和契约测试，确认权限、域名、输入文件、依赖、许可与实际行为一致。
4. 使用自己的本地身份签署整个目录，生成 `package.manifest.json`、
   `package.manifest.creator.sig` 和 `creator.identity`。
5. 需要市场发布时，明确接受 DCO，把同一份创作者签名包提交到市场仓库的
   `submissions/`。贡献者不得修改正式目录、作者记录、catalog 或维护者签名。

创作者签名证明包来自某把创作者密钥，但不代表市场审核或项目背书。

### 市场发布

外部 PR 的 CI 只进行规范 JSON、签名、哈希、路径、AST/YAML、依赖、许可、凭据泄漏和
DCO 静态检查，不 import 或执行投稿插件。维护者人工固定 manifest 哈希并完成审核后，
才在冷签名环境对同一份 manifest 原始字节复签。只有维护者整包签名和 catalog 签名都
有效的条目才属于市场发布态。

更新必须由同一创作者指纹签名，使用严格递增的 SemVer；禁止同版本覆盖、降级和换密钥
接管。完整流程见 [`docs/AUTHOR_GUIDE.md`](docs/AUTHOR_GUIDE.md) 和市场仓库的
[`CONTRIBUTING.md`](../OmniCrawler-market/CONTRIBUTING.md)。

### 安全边界

- 插件默认在独立子进程中运行，能力通过 SDK 代理；`in_process` 是限时、显式的高风险申请。
- 当前没有完整的 AppContainer/seccomp/Landlock 级 OS confinement，不能把子进程边界描述成
  对任意恶意代码的绝对隔离。
- 私钥、Token、Cookie 和真实凭据不得进入包、日志、测试素材或仓库。
- 网络和文件访问必须使用最小权限与精确白名单；权限扩大需要用户重新确认。
- 模板虽然不执行 Python，也必须经过整包签名、凭据、域名和数据来源条款检查。

## 测试规范

- 测试框架：pytest
- 测试目录：`tests/`，与 `src/` 结构对应
- 命名：`test_<module>.py`，测试函数 `test_<behavior>`
- 因环境缺失依赖而不可跑的测试用 `skip`（带原因），不要注释掉或删除
- 核心模块覆盖率目标 >= 85%

## 文件落点速查

| 你要做什么 | 放在哪里 | 不放在哪里 |
|-----------|---------|-----------|
| 某站点的特殊字段 | source/template 层 | 通用 parser |
| HTTP/浏览器/流式差异 | fetcher 层 | pipeline |
| PDF/OCR/语法解析 | parser/extractor/processor | fetcher |
| 字段清洗/归一化 | transformer | exporter |
| 输出格式（CSV/JSON/DB） | exporter | transformer |

## 构建与发布

**Windows 便携版构建详见 [`docs/WINDOWS_PACKAGING.md`](docs/WINDOWS_PACKAGING.md)。**
下面是最关键的规则：

1. **版本号唯一来源**：`src/omnicrawler/__init__.py` 中的 `__version__`。构建脚本自动读取，
   产物文件名由脚本生成，任何人（包括自动化工具）都不应在构建流程中手动修改版本号。
2. **修改版本号是独立操作**：使用 `tools/bump_version.py`，不与构建、测试、修复混在一起。
3. **产物归档**：所有构建产物放入 `artifacts/` 版本化目录，规则见 [`artifacts/README.md`](artifacts/README.md)。

### 快速构建命令（离线模式）

```powershell
# 当前版本号由源码决定，不要手动传入版本号
$python = "$PWD\.venv\Scripts\python.exe"

# Standard 便携 ZIP
.\build_windows.ps1 -Offline -Edition Standard -BuilderPythonPath $python `
  -BuildRootPath "$PWD\artifacts\build\{version}-standard-r1" `
  -ReleaseOutputPath "$PWD\artifacts\release\{version}" `
  -BrowserCachePath "$PWD\build_cache\browsers"

# Full 便携 ZIP
.\build_windows.ps1 -Offline -Edition Full -BuilderPythonPath $python `
  -BuildRootPath "$PWD\artifacts\build\{version}-full-r1" `
  -ReleaseOutputPath "$PWD\artifacts\release\{version}" `
  -BrowserCachePath "$PWD\build_cache\browsers" `
  -RuntimeCachePath "$PWD\build_cache\runtime"

# 源码 ZIP + wheel
.\.venv\Scripts\python.exe tools\build_source_archive.py
```

### 产物清单

每次构建生成 4 类产物，路径规则详见 [`artifacts/README.md`](artifacts/README.md)：

| # | 产物 | 典型路径 |
|---|------|---------|
| 1 | Standard 便携 ZIP | `artifacts/release/{version}/OmniCrawler-{version}-Windows-Portable-Standard.zip` |
| 2 | Full 便携 ZIP | `artifacts/release/{version}/OmniCrawler-{version}-Windows-Portable-Full.zip` |
| 3 | 源码 ZIP + wheel | `artifacts/python/{version}/OmniCrawler-{version}-Source.zip` |
| 4 | 完整便携目录（压缩前） | `artifacts/build/{version}-{edition}-rN/release/OmniCrawler/` |

## ADR（架构决策记录）

重要架构决策请记录到 `docs/adr/` 目录，使用模板 `docs/adr/0000-template.md`。

ADR 内容包括：上下文、决策、替代方案、后果。一旦写入不可修改（只能标记为 Superseded）。
