# 审查报告: packaging/scripts/configs

- 审查范围：OmniCrawler 0.3.0（Windows 便携打包 / 安装 / 启动脚本、Linux/macOS 源码环境脚本、Docker、pyproject、packaging/、tools/、configs/、schemas/、locale/、.github/workflows、相关 docs 与 .git* 配置）
- 审查方式：逐行阅读全部脚本与配置；PowerShell 5.1 Parser 语法检查（build_windows.ps1、install_windows.ps1、tools\prepare_windows_runtime.ps1、tools\add_template_version.ps1 通过）；tools\*.py 全部 py_compile 通过；两个 PyInstaller spec ast.parse 通过；关键路径 Test-Path / Get-ChildItem 实测；产物命名与 .venv 入口交叉核对
- 审查文件数：约 60 个（根目录 11 + Windows/Linux/macOS 脚本 15 + packaging/ 8 + configs 2 + schemas 1 + locale 2 + tools/ 25 + workflows 2 + docs 抽查 3 + src 关键文件 4）
- 结论：**纯审查，未修改任何文件**。发现 critical 1、high 7、medium 13、ux/low 6，共 27 项。最严重的是 Python 版本声明与实际语法不一致（3.10 声明、3.11+ 语法），以及 GUI 多语言链路整体失效（产物命名不匹配 + 未打包 locale）。

## 汇总

| 级别 | 数量 | 说明 |
| --- | --- | --- |
| critical | 1 | 会导致 3.10 用户/CI 直接失败 |
| high | 7 | 打包产物功能缺失、供应链/编码/启动脚本等实质缺陷 |
| medium | 13 | 文档与工具链不一致、维护性/可复现性问题 |
| low/ux | 6 | 体验与提示类问题 |
| **合计** | **27** | |

分级依据：critical = 必然触发且阻断用户主路径；high = 已打包/已发布产物存在明显功能缺陷或明显风险；medium = 不一致/不可复现/维护负担；low/ux = 提示与体验。

亮点（无问题部分）：版本号主链一致（pyproject.toml:9 `0.3.0` = src/omnicrawler/__init__.py:14 `0.3.0` = CHANGELOG.md `0.3.0` = docs/COMPATIBILITY_0.3.0.md）；build_windows.ps1 采用 UTF-8 BOM + `$ErrorActionPreference='Stop'` + `Assert-LastExit` + 构建后 6 项自检（--version、templates validate、capabilities、runtime-verify）；prepare_windows_runtime.ps1 具备原子写、`.part` 临时文件、缓存哈希记录；Dockerfile 双阶段 + 固定镜像 sha256 + 非 root 用户；.env.example 全部为空占位无真实密钥；spec 采用共享 COLLECT 目录避免重复二进制。

## 问题清单

### [critical] pyproject.toml:12 - 声明 Python >=3.10，但源码与工具大量使用 3.11+ 语法，3.10 用户与 CI 必失败

- 现状：`requires-python = ">=3.10"`（pyproject.toml:12）；README.md:23 与 docs/SUPPORT_MATRIX.md:7 声明支持 3.10；.github/workflows/quality.yml:19 matrix 含 `3.10`。但以下文件顶层直接 `from datetime import UTC`（3.11+ API，3.10 下 ImportError）：src/omnicrawler/core/utils.py:10（`datetime.now(UTC)` :18）、src/omnicrawler/core/logging_utils.py:5、src/omnicrawler/fetching/retry.py:6、src/omnicrawler/pdfx/utils.py:11、src/omnicrawler/quality/diagnostics.py:21、src/omnicrawler/scheduling/change_detector.py:37、src/omnicrawler/services/research_package.py:9、src/omnicrawler/templates/template_health.py:9。tools/ 顶层 `import tomllib`（3.11+）：build_source_archive.py:6、bump_version.py:40、check_docs_consistency.py:12、check_release_integrity.py:14、generate_checksums.py:23、generate_provenance.py:19（另用 `from datetime import UTC`）、generate_sbom.py:8、generate_release_info.py:13；build_windows.ps1:147 构建时内联 `import tomllib`。tools 中 `from datetime import UTC` 无 fallback。本机 `py -0p` 显示 3.10 存在但运行崩溃（exit=-1073741515，解释器损坏），无法实测，属静态证据。
- 问题：任何 `import omnicrawler.core.utils` 的模块在 3.10 下直接 ImportError；quality.yml 的 3.10 job 运行 pytest 必红（CI 实际只在 3.12/3.13 通过）；pip 在 3.10 下安装时虽能通过元数据检查，但运行即崩。`datetime.UTC` 无 `timezone.utc` 兼容层。
- 建议：二选一。① 将 `requires-python` 提升为 `>=3.11`，同步 README.md:23、docs/SUPPORT_MATRIX.md、tools/check_release_integrity.py 与 quality.yml（去掉 3.10）；② 若必须保留 3.10，为 8 处 `datetime.UTC` 与 8 处 `tomllib` 增加 3.10 兼容（如 `from datetime import timezone, datetime` + `UTC = timezone.utc`；`try: import tomllib except ModuleNotFoundError: import tomli as tomllib`），并新增 requirements 声明 `tomli; python_version < "3.11"`。

### [high] src/omnicrawler/gui/i18n.py:56 + tools/extract_i18n.py:92 + tools/compile_i18n.py:44 - GUI 多语言链路整体失效（domain 不匹配 + 目录多套一层 + locale 未打包 + 无 .mo）

- 现状：i18n.py:8 文档声明产物名 `locale/<lang>/LC_MESSAGES/omnicrawler.mo`，:56-58 实际 `_gettext.translation("omnicrawler", ...)` 查找 domain `omnicrawler`。但 extract_i18n.py 生成的 pot、generate_en_po.py 生成的 po、compile_i18n.py:44 输出的 mo 全部命名为 `omnicrawler-gui.*`（见 locale/omnicrawler-gui.pot、locale/en_US/LC_MESSAGES/omnicrawler-gui.po）。compile_i18n.py:44 的 mo_path 为 `po_path.parent / "LC_MESSAGES"`，即写成 `locale/en_US/LC_MESSAGES/LC_MESSAGES/omnicrawler-gui.mo`，比 gettext 约定多套一层。两个 spec（OmniCrawler.spec:14-19、OmniCrawler-Standard.spec:14-19）的 datas 仅含 templates/gui/templates/gui/help/stealth.min.js，**不含 locale/**；pyproject.toml:77-78 package-data 同样不含 locale。仓库当前无任何 .mo（Find -Filter *.mo 为空）。compile_i18n.py 依赖系统 msgfmt 外部命令。
- 问题：① domain `omnicrawler` vs 产物 `omnicrawler-gui` 永不匹配，翻译永远加载不到，切 en_US 界面仍显示中文；② 即使匹配，mo 放在 `LC_MESSAGES/LC_MESSAGES/` 下 gettext 也不认识；③ 便携版 datas 与 wheel 都不含 locale，修复 domain 后打包产物仍回退原文；④ 无 .mo 意味着当前 GUI 实际 100% 回退中文。
- 建议：① 统一命名：将 domain 改为 `omnicrawler-gui`（i18n.py:56）并把 i18n.py:8 注释改为一致；② compile_i18n.py:44 改为 `po_path.parent / "omnicrawler-gui.mo"`（即 `locale/en_US/LC_MESSAGES/omnicrawler-gui.mo`）；③ 两个 spec 的 datas 各加 `(str(project_root / "locale"), "locale")`；④ 打包流程在 build_windows.ps1 构建前自动运行 compile_i18n.py（或 `omnicrawler i18n compile`），并在 CI 校验 .mo 存在。

### [high] tools/prepare_windows_runtime.ps1:20-71,113-115,139 - 第三方二进制下载无预置信任锚（供应链 TOFU）

- 现状：Get-Asset 的 `$known = @{}`（:24）为空，仅从上次运行的 `.asset-hashes.json`（:25-28）回填（首次即信任=TOFU）；:35/:64 `$expected` 在无 `$Sha256` 且无缓存记录时为 `''`，:61 仅校验最小字节数。Tesseract 安装器（:113）、7zr.exe（:114）、7zip 安装器（:115）、tessdata_fast 语言包（:139）均为 https 直链但**无预置 SHA-256**，全新缓存下首轮下载零完整性校验；仅 playwright chromium（build_windows.ps1:164）有官方下载器自带校验。
- 问题：构建机每次全新下载 tesseract/7zip/chromedriver 时，被中间人/投毒源替换的文件可直接进入便携发布包并被打包进用户机器，违反供应链最小信任原则。
- 建议：在脚本顶部定义 `$KNOWN_SHA256 = @{ <Destination> = "<sha256>" }`，把 :108-110 三个文件与 tessdata 的正式哈希写死，Get-Asset 逻辑改为"无 expected 即 fail"（:35/:64 空哈希时 throw），并在 build_windows.ps1 调用处传入显式参数。

### [high] tools/add_template_version.ps1:1 - 硬编码其他项目绝对路径（死脚本/遗留物，删除风险）

- 现状：`$base = "E:\tool\biancheng\VScode project 3\omnicrawler2.1.0\source_extracted\OmniCrawler-2.1.0-Source\src\omnicrawler\templates"`，指向本机另一项目、且是 2.1.0 旧版本；脚本末尾 `Set-Content -NoNewline` 会用 PS5.1 UTF8 在文件头写 BOM。未被任何构建/CI 流程引用。
- 问题：① 泄露本机路径、与当前仓库无关；② 若在仓库根执行，`Set-Content` 会往其他项目的模板文件写 BOM，损坏该仓库文件；③ PowerShell 5.1 无 BOM 参数，编码不可控。
- 建议：删除该脚本；若确有"给模板写版本"需求，重写为仓库内路径 + `[IO.File]::WriteAllText(path, $content, [Text.UTF8Encoding]::new($false))`。

### [high] build_windows.ps1:209 + tools/check_architecture.py - 无 CPU 位数/架构断言；install_windows.ps1:17 `py -3` 无版本上界

- 现状：build_windows.ps1:209 调 PyInstaller 无 `--target-architecture`，spec 无 `target_arch`，构建前不校验解释器位数（:112-120 仅检查 python 存在并创建 venv）；tools/check_architecture.py 实际做的是模块依赖关系 AST 检查，与"架构"无关，命名误导。install_windows.ps1:17 `py -3` 取系统最新 Python（本机 `py -0p` 默认 3.14），full extra 中 paddlepaddle/ddddocr 等对 3.14 可能无 wheel。
- 问题：若构建机是 32 位 Python，静默产出 32 位便携包；源码安装 `py -3` 选中过新版本时 pip 报混淆的 "No matching distribution"，用户无法定位。
- 建议：① build_windows.ps1 在 venv 创建后断言 `[Environment]::Is64BitOperatingSystem` 且 `python -c 'import struct; sys.exit(0 if struct.calcsize("P")==8 else 1)'`；② install_windows.ps1 校验解释器 >=3.11 且 <4，失败时给出明确提示与 `py -3.12 -m venv` 示例；③ 或给 check_architecture.py 改名（如 check_dependencies.py）避免与 CPU 架构混淆。

### [high] 全部 .bat 工作树为 LF + build_windows.ps1:238 原样打包；tools/prepare_windows_runtime.ps1 无 BOM UTF-8 中文在 PS5.1 乱码

- 现状：实测仓库所有 .bat（setup_windows.bat、run_*.bat、packaging\OmniCrawler-Launcher.bat）均为 LF 行尾（.gitattributes:8 声明 `*.bat text eol=crlf`，但当前工作树未转换；若用户下载 GitHub ZIP，.gitattributes 也不参与，得到的仍是 LF）。build_windows.ps1:238 用 Copy-Item 原样拷贝 Launcher.bat 进发布 ZIP。Launcher.bat 含 `:wait_loop`/`:started`/`:failed` 标签与 `goto`/括号块，LF-only 批处理在这些构造上存在已知解析问题（标签匹配/延迟扩展）。
- 问题：① 便携包内的 .bat 若为 LF，在用户 cmd 下可能出现 goto 失效、括号块执行异常；② tools/prepare_windows_runtime.ps1 为无 BOM 的 UTF-8（含中文），Windows PowerShell 5.1 按 ANSI(GBK) 解码，:67 "SHA-256 校验失败"、:154、:162 等关键 throw 消息在内存中即成乱码——恰是安全提示与失败路径。build_windows.ps1 带 UTF-8 BOM 解析正确，但中文输出在默认 GBK 控制台同样显示乱码。
- 建议：① build_windows.ps1 在打包 .bat 时显式转换为 CRLF（读文本后写为 `"`r`n"`）；② 为 install_windows.ps1、prepare_windows_runtime.ps1 加 UTF-8 BOM，或将其中文消息改为英文；③ 在构建 CI 用 `git check-attr eol` 断言 bat 按 CRLF 检出。

### [high] run_windows.bat:4-7 / run_workbench_windows.bat:4-7 与 run_gui_windows.bat:11-15 行为矛盾；run_workbench_*.sh/.bat 不转发参数

- 现状：run_windows.bat 与 run_workbench_windows.bat 强制要求 `.runtime\python\python.exe`（bundled）存在，源码安装（用系统 python 建 .venv）时直接报错退出；run_gui_windows.bat:11-15 有 `rebase_venv.py` 回退到普通 .venv 的路径。run_workbench_windows.bat:13、run_workbench_linux.sh 不转发 `%*`/`"$@"`，而 run_linux.sh:8、run_gui_linux.sh:10 会转发。
- 问题：同一安装来源下三个启动脚本对"无 bundled runtime"的处理不一致；`omnicrawler-workbench` 无法带参数启动，脚本行为与其余入口不统一。
- 建议：统一三个 Windows 启动脚本的逻辑（存在 .runtime\python 时 rebase，否则直接 `.venv\Scripts\python.exe -m omnicrawler ...`）；run_workbench_windows.bat 与 run_workbench_linux.sh 补上参数转发。

### [high] packaging/OmniCrawler.spec:77 + OmniCrawler-Launcher.bat - 便携 GUI 崩溃无任何反馈

- 现状：GUI EXE `console=False` 且 `disable_windowed_traceback=False`（spec:77-78）；Launcher.bat 仅 `tasklist` 轮询 60 秒判断进程出现，超时打印 "未在 60 秒内启动"。
- 问题：GUI 启动即崩溃/缺 DLL 时，用户看不到任何 traceback（windowed 模式 + 无 windowed_traceback 文件），Launcher 也没有引导查看 `%TEMP%\*` 日志的路径，便携版排障只能盲猜。
- 建议：① 开启 windowed_traceback（或打包 `python 未找到` 自检 EXE）；② Launcher.bat 在超时后定位并提示最近的 `%LOCALAPPDATA%\Temp\OmniCrawler*` 日志文件路径；③ 在 build 后自检阶段模拟一次 GUI 启动并检查退出码。

### [medium] configs/full_pipeline.yaml:52 - 引用不存在的配置文件

- 现状：`processors.pdf.config: configs/pdf/announcement_fields.yaml`，实测 `configs\pdf` 目录不存在（Test-Path False），该文件为示例模板中唯一指向自定义 PDF 字段映射的入口。
- 问题：按文档步骤复制该模板运行完整流水线会直接报"配置读取失败"，用户无法从模板学到 PDF 字段映射的写法规格。
- 建议：在 configs/pdf/ 下补 announcement_fields.yaml 示例，或改为 `configs/pdf/announcement_fields.yaml.example` 并在注释说明；同时让配置加载器对缺失文件给出"参照 configs/pdf/xxx.example 创建"的错误提示。

### [medium] tools/generate_checksums.py:27-37 - 校验产物名与仓库实际不符，docstring 残留旧版本

- 现状：`REQUIRED_PATTERNS` 期望 `ChangeLog.md`、`Quick-Start.md`、`Release-Report.md`、`Test-Report.md`，而仓库实际为 `CHANGELOG.md`、`docs/INSTALLATION.md`、`docs/releases/RELEASE_REPORT_*.md`（无 Quick-Start/Test-Report）。docstring 残留 "1.1 → 2.1"、"2.1.0" 旧版本。
- 问题：`--verify` 恒报 4 项缺失；quality.yml:47-48 用 `--check` 分支绕开 verify，问题被掩盖。
- 建议：将 REQUIRED_PATTERNS 对齐实际产物（或按目标 release 目录动态发现）；更新 docstring；CI 同时跑 `--verify`。

### [medium] .gitignore:21-24,61 - `.omnicra/` 笔误与无路径锚点

- 现状：:21 `.omnicra/`（拼写残缺，:61 `.omnicrawler/` 已覆盖）；:23 `_internal/`、:24 `browsers/` 无前导 `/`，可匹配任意层级同名目录；:22 `.runtime/`、:25 `/runtime/` 并存。
- 问题：`.omnicra/` 是死条目；`_internal/` 无锚点会误忽略将来 `src/.../internal/` 等目录（当前无此名目录，属潜在）。
- 建议：:21 删除；:23-24 改为 `/_internal/`、`/browsers/` 锚定根目录。

### [medium] pyproject.toml:101,126 - ruff/mypy 目标版本 py313 与 requires-python >=3.10 不一致

- 现状：`[tool.ruff] target-version="py313"`、`[tool.mypy] python_version="3.13"`，而项目声明 >=3.10。
- 问题：3.10 不兼容的语法（如 `datetime.UTC`、更高级类型语法）会静默通过 lint/类型检查，CI 中 3.10 job 又因 ImportError 崩溃，等于 3.10 支持从未被真正保障。
- 建议：与 C1 联动，若降 3.11 则 ruff/mypy 目标同步 `py311`；若保留 3.10 则目标 `py310` 以强制兼容。

### [medium] pyproject.toml:58 vs 30-35 - 依赖约束不统一且无锁文件

- 现状：full extra 内 `"PyQt6>=6.5"`、`"ruamel.yaml>=0.17"`、`"psutil>=5.9"`、`"requests>=2.28"`（:58）无上界，而 gui extra 对应项（:30-35）有 `<7`/`<6` 等上界；runtime 依赖无 requirements.txt / lock 文件，constraints/quality.txt 仅锁定 dev 工具链。
- 问题：`pip install .[full]` 与 `.[gui]` 解析出的 PyQt6 版本可能不同，且升级新主版本不可复现；Docker 构建与源码安装之间的依赖版本漂移。
- 建议：给 :58 各项补与 gui extra 相同的上界；考虑引入 pip-tools/uv 生成 `requirements-full.lock`（尤其为 Dockerfile 与便携构建提供固定版本）。

### [medium] CHANGELOG.md - 结构混乱（Unreleased 空节、版本乱序、历史噪音）

- 现状：`## Unreleased` 节为空后紧跟 `## 0.3.0 - 2026-08-05`；0.1.1 条目位于 0.2.0/0.3.0 之后且内容为空；0.2.0 节含 "release: bump to 0.1.0"、"bump to 2.8.0" 等应被 bump_version.py 过滤的 commit 噪音。
- 问题：读者难以定位当前版本变更；0.1.1 空条目与乱序破坏 changelog 惯例。
- 建议：删除 Unreleased 空节与 0.1.1 空条目；将 0.2.0 节降为历史记录标题；为 bump_version.py 增加对 CHANGELOG 的 commit 清洗过滤。

### [medium] README.md:77,26 + tools/check_cli_docs.py:14-20 - 死链与固定 Python 版本示例

- 现状：README.md:77 链接 `docs/USER_GUIDE_2.0.md`（不存在）；check_cli_docs.py:14-20 DEFAULT_DOCS 引用 `docs/USER_GUIDE.md`、`docs/USER_GUIDE_2.0.md`（均不存在）；README.md:26 源码安装示例固定 `py -3.10`，与 install_windows.ps1:17 的 `py -3`（取最新）不一致。
- 问题：两个文档链接断裂；3.10 示例与 C1（代码实为 3.11+）冲突，用户照抄即失败。
- 建议：README:77 改为实际存在的 `docs/INSTALLATION.md`；check_cli_docs.py 改为引用现有文档；README:26 与 install_windows.ps1 统一 Python 版本策略（建议都写 `py -3.11` 或更高）。

### [medium] setup_linux.sh:25 / setup_macos.command:22 - 硬编码版本号 "OmniCrawler 1.0.0"

- 现状：两者成功提示为 `echo "OmniCrawler 1.0.0 full Linux/macOS source environment is ready."`，当前项目版本为 0.3.0；setup_linux.sh:9 `playwright install --with-deps chromium` 需要 apt/sudo 且会改写系统包。
- 问题：成功提示版本号与 README/CHANGELOG 冲突，误导用户；非 Debian/rootless 环境 `--with-deps` 行为不明，无降级路径。
- 建议：改为从 pyproject.toml 读取版本（`grep '^version' pyproject.toml`）或更新为 0.3.0；为 `--with-deps` 增加失败提示"请手动安装系统依赖或使用 venv 内浏览器"。

### [medium] Dockerfile:18-19 - 不可复现且遗漏 3 个 console scripts 入口

- 现状：Dockerfile:18 `COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages` 整包拷贝，依赖版本仅受 pyproject 区间约束；:19 `COPY --from=builder /usr/local/bin/omnicrawler*` 的 glob 只匹配 `omnicrawler`、`omnicrawler-gui`、`omnicrawler-workbench`，而 pyproject.toml:66-69 还定义了 `pdfx`、`pdf-process`、`pdf-extract` 三个入口脚本，均未复制进最终镜像。
- 问题：① 镜像重建结果随上游依赖漂移；② 镜像内 `omnicrawler pdf*` 相关命令缺失（若 CMD/用户按文档使用会 command not found）。
- 建议：:18 前改为 `pip freeze`/lock 文件安装；:19 复制 `pdfx*`、`pdf-process*`、`pdf-extract*` 或直接 `COPY --from=builder /usr/local/bin/ /usr/local/bin/`（注意不要覆盖运行时 python 二进制）；并为镜像补充 `docker build` 后的 `omnicrawler --version` 自检。

### [medium] locale/en_US/LC_MESSAGES/omnicrawler-gui.po - 未翻译条目 msgstr 保留中文原文，English 界面仍显示中文

- 现状：po 共 541 条 msgid；词典覆盖不全，未翻译条目 msgstr 与 msgid 相同（抽样如 `msgstr "%p% 非空"` 即中文原文）；extract_i18n.py 生成的 .pot 中 `#:` 路径为 Windows 反斜杠（如 `src\omnicrawler\gui\...`），跨平台 msgmerge 兼容但路径不一致。
- 问题：en_US locale 即便打包成功，仍有大量字符串以中文显示，多语言质量未达发布标准；反斜杠路径影响 msgmerge 去重。
- 建议：补齐 en_US 翻译或对未翻译条目在代码层回退英文 UI 文案；extract_i18n.py 生成 posix 风格相对路径（用 `/`）。

### [medium] build_windows.ps1:135-137 - 依赖 import 完整性检查覆盖不全

- 现状：Full 分支 :135 检查 `PyInstaller, paddleocr, selenium, PyQt6, pyarrow, psycopg, opensearchpy`；Standard 分支 :137 检查 `PyInstaller, PyQt6, playwright, fitz, openpyxl`。二者均未检查 duckdb、redis、scrapy、ddddocr、crawl4ai、onnxruntime 等 full 关键包；且该检查只在 `-SkipDependencyInstall` 分支执行（:126 注释），自动安装分支无等价校验。
- 问题：自动安装流程中若某个 full 包安装失败（如 paddlepaddle 无当前 Python wheel），要到冒烟测试（:272-294）才暴露，定位成本高。
- 建议：将 :135-137 的 import 检查改为在所有构建路径执行，并补齐 full 包清单；依赖清单与 pyproject full extra 之间用脚本同步，避免漂移。

### [medium] build_windows.ps1:32-33,323-329 - 构建临时目录与浏览器缓存不清理，磁盘占用累积

- 现状：默认 `$buildRoot = %TEMP%\OmniCrawler-build-full`（:26-30），含 builder venv（`OmniCrawler-build-full-venv`）与 `browsersRoot`（~1GB+ Chromium，:33）；finally（:324-329）仅还原 `PLAYWRIGHT_BROWSERS_PATH`，不删除 buildRoot。
- 问题：每次构建在系统盘残留数 GB 数据；`Copy-VerifiedTree`（:167）每次全量复制浏览器缓存到临时目录，重复 IO。
- 建议：构建成功后删除 `$buildRoot`（保留 `build_cache\browsers` 作为缓存）；提供 `-KeepBuildRoot` 开关；复制浏览器改为硬链接/Junction 以减少拷贝。

### [medium] packaging/OmniCrawler.spec / OmniCrawler-Standard.spec - 三份独立 Analysis 重复收集 + 可选模块未隔离

- 现状：两个 spec 均为 gui/cli/worker 各跑一次 `Analysis`（OmniCrawler.spec:65,81,97；Standard.spec:43,51,59），三次扫描同一源码树；`collect_submodules("omnicrawler")`（Standard.spec:20）会把全部 omnicrawler 子模块（含 omnicrawler.ocr.paddle、omnicrawler.apps.pdf_processor 等）纳入收集，而 excludes（Standard.spec:21-26、Full.spec:61）只能排除第三方包。
- 问题：① 构建时间三倍化（Full 版 paddle 等大数据被三份共享 COLLECT 收集一次二进制，但纯 Python 扫描三次）；② 若某可选模块顶层 import 被 exclude 的包（如 Full 排除 torch 但 paddle 生态依赖 torch），分析阶段即失败或运行时缺模块，且 Standard 版无法把我们的可选模块排除出包体。
- 建议：仅对三个入口各建一个 `Analysis` 是 PyInstaller 标准做法，可接受；但建议给可选模块补 `try/except ImportError` 保护（spec 分析期即验证），并在 Standard 版用 `excludes` 列出 `omnicrawler.apps.pdf_processor` 等不必要模块以控制体积；CI 对 Standard/Full 各跑一次构建验证。

### [low/ux] run_workbench_linux.sh:4 - 无 venv 存在性检查

- 现状：直接 `".venv/bin/python" -m omnicrawler workbench`，而 run_linux.sh:4-7 有缺失检查与中文提示。
- 问题：未安装环境下报 "No such file or directory" 类晦涩错误。
- 建议：补齐与 run_linux.sh 一致的检查与提示。

### [low/ux] install_windows.ps1:36-40 - `-Minimal` 模式无功能降级提示

- 现状：-Minimal 跳过 Chromium 与 runtime 资产，但安装完成后无任何"动态采集/OCR 不可用"提示，run_gui_windows.bat 仍正常启动 GUI。
- 问题：用户可能误以为 Minimal 等于 Full 功能。
- 建议：安装完成输出清单，注明 Minimal 缺哪些能力及如何补装（`tools\prepare_windows_runtime.ps1`）。

### [low/ux] .env.example - 无逐项注释，`PDFX_LLM_BASE_URL` 默认值易误读

- 现状：仅首行一句说明，其余变量无用途注释；`PDFX_LLM_BASE_URL=https://api.openai.com/v1` 等默认值易被误认为必填或已配置。
- 问题：用户照抄 .env.example 时无法判断哪些必填、哪些可选。
- 建议：为每个变量加一行注释（必填/可选、示例），对含密钥变量明确"留空以禁用"。

### [low/ux] packaging/OmniCrawler-Launcher.bat:31 - 失败提示无日志定位指引

- 现状：超时提示含"可能缺少 DLL 或被安全软件拦截"，但未指出查看日志的具体路径。
- 问题：排障时用户不知道日志在 `%LOCALAPPDATA%\Temp` 或 GUI 数据目录。
- 建议：提示行补充 "详细日志：%LOCALAPPDATA%\OmniCrawler\logs（如存在）"。

### [low/ux] packaging/PORTABLE_README.txt - 无卸载/升级/备份说明

- 现状：说明 Standard/Full 差异与运行方式，但无卸载、版本升级、数据备份指引。
- 问题：便携版（单目录 + %APPDATA% 状态）用户升级时不知如何保留工作区。
- 建议：补充"升级=替换目录内文件、保留 browsers/ 与 _internal"及"卸载=删除目录并清理 %APPDATA% 工作区"说明。

### [low/ux] setup_windows.bat:7 - 失败提示为英文

- 现状：失败分支 `echo [ERROR] Installation failed. See the message above.` 为英文，与其余中文提示混排；且双击运行时长 pip 阶段无进度安抚提示。
- 问题：中文用户看到英文错误且不知下一步。
- 建议：改为中文并提示查看上方报错/重跑 `install_windows.ps1` 捕获日志。
