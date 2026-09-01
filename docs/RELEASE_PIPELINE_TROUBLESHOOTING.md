# OmniCrawler 发布流水线故障排查手册（v0.9.1 全轮次复盘）

> 生成于 2026-08-20，v0.9.1 发布成功后。覆盖 2026-08-16 ～ 08-20 共 **40+ 轮
> release 流水线迭代、90+ 提交**的全部问题/根因/解法/试错经验。
> 目标：**同类问题再现时一轮直接命中，不再重走弯路。**

---

## 0. 最终发布链（当前正确状态，勿改）

```
push main
  → quality.yml（6 job：test×3 平台 / docker / windows-full-dependency-matrix / gui-and-browser）
  → e2e.yml（Python 3.12 / Chromium E2E）
  → 两者全绿后才可：git tag -f v<ver> <commit> && git push --force origin refs/tags/v<ver>
  → release.yml（verify-python-version → build-{windows,linux,macos}-portable → release 聚合 job）
  → softprops/action-gh-release 发布到 GitHub Release
```

**红线**：quality + e2e 未全绿，禁止移动 tag；禁止为通过构建削弱任何测试/校验。

最终产物（v0.9.1 实测）：

| 产物 | 大小 | 格式要点 |
|---|---|---|
| Windows Full / Standard | 1878MB / 477MB | zip |
| Linux Full / Standard | 1714MB / 379MB | **tar.xz**（tar.gz 曾 2095MB 超 GitHub 2GiB 上限） |
| macOS Full / Standard | 1082MB / 825MB | dmg（hdiutil 失败回退 tar.gz） |
| 配套 | - | provenance + SBOM×3 + SHA256SUMS×3 |

---

## 1. macOS 构建链（16 轮，从未成功 → 全绿）

macOS .app 构建是阶段 6 新增（e5fa11e），v0.8.0 只发过 Windows，**macOS 链路从未生产验证过**。

### 1.1 Frameworks/Python 缺失（PYI-5670 / PYI-43699）【最高频根因】
- **现象**：`.app` 缺 `Contents/Frameworks/Python`，启动报 PYI-5670/43699。
- **弯路**：先怀疑镜像漂移（pin macos-latest→macos-14，无效）；再怀疑 Python 版本（强制 python@3.12，无效）；再试 `BUNDLE(3×EXE, binaries=...)` 显式传 binaries（commit 88a2418，**仍无效**）。
- **真因**：macOS spec 结构不对。PyInstaller macOS 标准是
  `COLLECT(exe, binaries, datas)` → `BUNDLE(coll)`；直接 BUNDLE 传 EXE + binaries
  不会把 Python.framework 拷进 Frameworks。
- **正解（f2e50ba）**：`COLLECT(gui_exe, cli_exe, worker_exe, ...binaries, ...datas)`
  然后 `BUNDLE(coll, name=...)`。Windows/Linux 的 COLLECT 一直是这个结构，macOS 漏了。
- **注意**：python@3.13 有 PYI-5670 回归（6.15），framework Python 候选必须排除 minor>13，
  且 build_macos.sh 对 python@3.12 加硬断言（与 BUILD_PYTHON_VERSION 一致）。

### 1.2 codesign 与 Chromium bundle
- `codesign --deep` 对 Chromium.app 报 bundle ambiguous（b7cc72b）→ 逐层签名。
- browsers 放进 .app 内与 codesign seal 冲突（4828e12）→ browsers 移到 .app 同级
  （release 根），运行时经 `runtime_paths.bundled_browser_executable()` 探测 sibling。
- **关键规则**：Chromium 可执行文件需单独 ad-hoc 签名（590b2b1）；签名顺序：
  先内层 Chromium .app → 再可执行文件 → 最后主 .app。

### 1.3 Chromium 可执行文件名（8cbb888）
- 新版 Playwright macOS 下载 `chrome-mac-arm64.zip`，可执行文件是
  `Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing`。
- **三平台可执行名不同**：Linux `chrome`、Windows `chrome.exe`、macOS 新版
  `Google Chrome for Testing`。构建期 find 与运行时 glob **两侧都要覆盖且对称**。

### 1.4 bash 3.2 兼容（GitHub macOS runner /bin/bash 是 3.2）
- `declare -A` 关联数组 → invalid option（37a8ced 改普通数组 + _seen() 函数）。
- `${arr[@]}` 空数组 + `set -u` → unbound variable（42e0cf9 先判 `${#arr[@]} -eq 0`）。
- 递归函数内局部变量 + set -u → unbound（82233f3 直接移除 set -u，显式 die 兜底）。
- **规则**：跨平台脚本必须按 bash 3.2 写；宁可移除 set -u 用显式校验。

### 1.5 dylib 拷贝（637950d）
- brew dylib 权限 555 + 同一物理库多路径解析 → 重复 cp 报 Permission denied。
- **正解**：cp 加 `-f`（BSD cp 强制覆盖只读目标）；去重按 **basename** 而非完整路径。
- @rpath 依赖解析失败不能 die（e06fea1）：降级为警告+跳过，否则整棵树构建中断。

### 1.6 runtime-verify unknown / corrupt（4264b6f / 9a89d66）
- create 侧用 `Path.resolve()` 记 symlink 解析后路径，verify 侧按磁盘 symlink
  相对路径扫 → macOS .app 内 Frameworks 全是 symlink → 大量 unknown。
  **正解**：create 侧不 resolve，与 verify 对称。**manifest 创建/校验必须同一套路径规则。**
- logs 排除前缀匹配覆盖不到 `Contents/MacOS/logs/` → 改任意层级目录段匹配。
  后续修正（650df33）：只排除真正的运行期日志目录（顶层 logs/ 与 Contents/MacOS/logs），
  botocore/data/logs、xet/logs 是合法服务数据，误排除导致 ZIP/tar 与 manifest 不一致。

### 1.7 macOS Full 定位
- paddle 无 Intel Mac 支持、M 系需 paddlepaddle-macos → macOS 走**弱 Full**：
  不打包 paddle，PaddleOCR 以 Transformers 后端替代；self-test 中 paddle_structure
  标记 skipped（2e5f0d2），深校验 `requires_paddle = platform != mac`。

---

## 2. Linux Paddle 库加载（7 轮弯路 → 正解）【最曲折的坑】

### 症状
`capabilities --self-test` 报：
```
RuntimeError: (PreconditionNotMet) The third-party dynamic library
(libmklml_intel.so) that Paddle depends on is not configured correctly.
(error code is libmklml_intel.so: cannot open shared object file)
(dynamic_loader.cc:409)
```

### 已证伪的方案（勿再尝试）
| 方案 | 提交 | 为什么错 |
|---|---|---|
| patchelf --set-rpath '$ORIGIN' | 6239923 | **裸名 dlopen 不查 RPATH**；RPATH 只对"库加载自身依赖链"生效。paddle 用 `GetDsoHandleFromSearchPath(FLAGS_mklml_dir="空", "libmklml_intel.so")` 裸名 dlopen |
| runtime hook 运行时注入 LD_LIBRARY_PATH | bbfb905 | **glibc 只在进程启动时解析 LD_LIBRARY_PATH 一次**（缓存进 __rtld_env_path_list.dirs），运行时改 os.environ 无效 |
| ctypes.CDLL 绝对路径预加载 | 60937bf→3433cc5 四轮 | 诊断日志实锤：12 库 CDLL 全部 loaded 成功，paddle 裸 dlopen **依然失败**。libmklml_intel.so 无 SONAME，CDLL 加载与裸名 dlopen 的已加载库匹配不命中 |

### 弯路中的中间发现（保留为知识）
- 全量预加载时错误变成 `flags error: flag defined both in profiler.cc`——
  是 libphi/libphi_core 双重加载所致（flag 静态链接进 .so），证明预加载"能加载"但方向错。
- libmklml_intel.so 的 NEEDED 唯一非系统依赖是 libiomp5.so。

### 正解一：FLAGS_*_dir 环境变量（bdde39a）——最干净
- `mklml_dir`/`lapack_dir` 是 paddle 官方 `PHI_DEFINE_EXPORTED_string` flag，
  gflags `flagsFromEnv` 在 import paddle 时读取同名环境变量。
- **实证**：`FLAGS_mklml_dir=X python -c "import paddle; paddle.get_flags('FLAGS_mklml_dir')"` 返回 X；
  libpaddle.so 含 `flagsFromEnvEv` 符号、libphi_core.so 含 flag 名字符串。
- 实现：`runtime_paths.configure_runtime_environment()`（Linux frozen、paddle import 前）
  `os.environ.setdefault("FLAGS_mklml_dir", str(bundle_root()/"paddle"/"libs"))`。
  设置后 paddle 用**绝对路径** dlopen，彻底绕开裸名查找。

### 正解二（历史方案，已撤）：copy-to-_internal-root（c75460f）
- 把 paddle/libs/*.so 复制到 `_internal` 根，利用 bootloader 启动时把
  `sys._MEIPASS` 前置进 LD_LIBRARY_PATH（PyInstaller 官方机制）。
- CI 实证有效（libmklml 加载成功），但**让 Full 包 +150MB 压缩后**，
  最终触发 GitHub 2GiB 上限问题（见 §6）→ 已替换为 FLAGS 方案。

### 相关：oneDNN/PIR 推理崩溃（f85fe26 / 9804baf）
- 库加载成功后出现：`NotImplementedError: ConvertPirAttribute2RuntimeAttribute
  not support [pir::ArrayAttribute<pir::DoubleAttribute>] (onednn_instruction.cc:116)`。
- 根因：Paddle 3.3 oneDNN+PIR 执行器不支持部分 PPStructureV3 detector 属性转换。
- **正解**：`PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT=False`，**三平台**都要设
  （Windows 先有，Linux/macOS 后补；download_and_smoke_test.py 也需设）。

---

## 3. Windows 构建链

### 3.1 scipy hidden import（1e64abe，Linux 侧镜像修复 943f0e2）
- 现象：`WARNING: Hidden import "scipy._lib.array_api_compat.numpy.fft" not found`
  → 产物内 `import scipy` 失败 → capabilities `--verify-imports` 判 false → exit 1。
- 根因：scipy 1.18 内嵌（vendored）`scipy/_external/array_api_compat`，
  PyInstaller 静态扫描看不到其内部子模块。
- **正解**（三处收集）：
  ```python
  hiddenimports += collect_submodules("scipy")
  hiddenimports += collect_submodules("scipy._external.array_api_compat")
  hiddenimports += collect_submodules("array_api_compat")
  ```
- 定位技巧：capabilities stdout 被 `> CAPABILITIES.json` 重定向吞掉细节，
  需翻 PyInstaller 构建日志找 `WARNING: Hidden import`。

### 3.2 paddle libs 收集（01ea867，Linux Full spec）
- `collect_all("paddle")` 会漏 paddle/libs/*.so（PaddleOCR discussion #11342 同因）。
- 显式补：`collect_dynamic_libs("paddle")` + `collect_data_files("paddle", includes=["libs/*"])`。
- 注意 PyInstaller 版本参数名差异：6.15 是 `include_py_files`（单数），7.x 才是
  `includes_py_files`（fe842d4 笔误教训）。

### 3.3 CLI 与 GUI 可执行名大小写冲突（9275b7c/380e782/c430166）
- 改名 omnicrawl→omnicrawler 后，Linux onedir 内 `OmniCrawler`（GUI）与
  `omnicrawler`（CLI）在大小写不敏感平台会撞。
- CLI 改名 `omnicrawler-cli`；integrity 判重 Windows/macOS 保留 casefold、
  Linux 大小写敏感（b2fd059，`_portable_path_key(case_sensitive=platform=='linux')`）。

### 3.4 PDX 重复初始化（1e64abe）
- PPStructureV3 防重初始化保护：capabilities self-test 构造 pipeline 时
  "PDX 已初始化"应视为 ok 而非报错。

---

## 4. selenium BiDi 上游 bug（selenium 4.47 + Chrome 151）

- 现象：selenium 引擎冒烟拦截/渲染超时，三平台偶发挂起。
- 试错链（全部无效）：--disable-gpu → pageLoadStrategy=none+加大超时 →
  看门狗 fail-closed → --enable-bidi+禁后台定时器节流 → BiDi 订阅竞态 guard。
- **结论**：是 selenium 4.47 BiDi 的上游平台 bug，**三平台统一跳过 selenium 引擎冒烟**
  （8245a9a/4c8e6e3/232c016），与 Playwright 冒烟并存。勿再尝试修复 BiDi 路径，
  等 selenium 上游修复后再评估。
- 冒烟工具增强：`OMNICRAWL_SMOKE_LIVE=1` 实时透传日志（f6c0671）。

---

## 5. 完整性校验体系（check_release_integrity / runtime manifest）

### 5.1 判定标准（勿削弱）
hash/size/symlink/duplicate/missing 检出逻辑全部保留；修复产物不合规，而非放宽校验。

### 5.2 性能：tar 深校验 O(N×体积) 退化（362e9ee）
- 现象：Standard 深校验 59 分钟、Full（10805 文件）120 分钟 job 超时跑不完。
- 根因：按 manifest 排序逐条对 gzip 流 `extractfile()` 随机读——gzip 反向 seek =
  从头重解压，退化为 O(条目数×包体积)；zip 有中央目录无此问题。
- **正解**：单遍前向流式扫描——按存储顺序读内容流式算 SHA-256（1MB 分块、内存恒定），
  小元数据（EDITION.txt / RUNTIME-MANIFEST.json / omnicrawler-model-manifest.json，
  ≤16MB 门禁）缓存原文供结构校验。实测 19x 提速。
- 本地复现注意：内存 BytesIO seek O(1) 复现不了，必须用**磁盘 gzip 文件**做基准。

### 5.3 symlink 不得进便携包（49bdbf9）
- prepare_linux_runtime.sh 有意用 `cp -a` 保留「短名 symlink → 版本号真身」
  （DT_NEEDED 引用短名）——暂存期合法；但 `cp -r` 拷入发布目录漏了 `-L` →
  symlink 原样进 tar（size=0）→ 深校验三重报错（symlink+size mismatch+unreadable）。
- **正解**：进包一律 `cp -rL` 解引用（短名成为真身副本，DT_NEEDED 按名命中不变，
  RPATH $ORIGIN 不受影响）。macOS 同理（dylib 拷贝）。

### 5.4 manifest 与 tar 的排除规则必须一致
- tar 打包排除运行期 logs/（b8f1cd1），与 create_runtime_manifest 的
  `_EXCLUDED_LOG_DIRS` 完全对齐，否则双向对账失败。

---

## 6. 发布到 GitHub Release

### 6.1 单文件 2GiB 硬上限（bdde39a + 0043611）
- 错误：`Validation Failed: size must be less than 2147483648`。
- 诊断方法：`gh api .../artifacts/<id>/zip` 下载真实产物逐字节分析体积构成。
- v0.9.1 实测（4.2GB 解压）：runtime/models 1038MB > _internal/paddle 688MB >
  browsers 648MB > playwright 133 > tesseract 131 > PyQt6 101 > cv2+opencv 179。
- **gzip 已用尽**（-6→2095MB，-9→2085MB）。
- **正解**：tar.gz → **tar.xz**（preset=6 实测 2095→1714MB，余量 334MB），
  零内容删减。联动改 5 处：build_linux.sh（tar -cJf）、check_release_integrity
  （glob *.tar.xz+*.tar.gz，macOS 回退兼容）、generate_checksums
  （_PLATFORM_REQUIRED）、release.yml（深校验 glob+安装说明 tar -xJf）、
  docs/PORTABLE_PACKAGING.md。
- `tarfile.open(path, "r:*")` 自动检测 xz，单遍流式深校验无需改。
- **体积预算意识**：任何往包里加内容的改动（如复制 .so），先算压缩后增量。

### 6.2 供应链门禁（700cab0）
- SBOM 生成：传递 BFS 必须按 pip 语义处理 extra——`extra == "e"` 声明仅当
  拉入边请求了 e 才展开（否则 httpx[cli] 的 click 泄漏进 SBOM；windows runner
  系统 Python 预装 click/colorama 时幽灵条目混入）。
- market 仓缺席语义：聚合 job 不 clone market 是合法状态（联网市场），
  MARKET_ROOT 不存在时门禁打 NOTE 跳过，**market 在场时一字不降**。
- 同环境比对：跨平台 SBOM 比对天然脆弱；只用 Linux SBOM（ubuntu-22.04+3.12+
  同 extras，隔离 venv 不污染）与聚合 job 环境比对。

### 6.3 GPG 签名步骤（abcdfdc）
- `secrets` 上下文不可用于 `if:` 条件表达式 → 步骤内用 shell 判断
  `[ -z "${GPG_PRIVATE_KEY:-}" ]` 跳过。

### 6.4 workflow_dispatch 不可用于发布
- release.yml 发布步骤 `tag_name: ${{ github.ref_name }}` 对 dispatch 会误用
  分支名当 tag → **发布只能走 tag push**。

### 6.5 tag 更新流程
- `git tag -f v<ver> <commit>` **先更新本地 tag**，再
  `git push --force origin refs/tags/v<ver>`；否则 "Everything up-to-date"。
- 旧格式 asset 残留：softprops overwrite 只覆盖同名，改产物扩展名后需手动
  `gh release delete-asset` 删旧格式文件。

---

## 7. CI 基础设施经验

| 问题 | 解法 |
|---|---|
| ubuntu-latest 漂移（glibc 2.39→产物旧系统跑不了） | Linux 三处锁 `ubuntu-22.04`（glibc 2.35） |
| macos-latest 非确定性失败 | 固定 OS 代际；`macos-14` 退役前迁移并锁定 `macos-15`（arm64） |
| macOS framework Python | homebrew python@3.12 硬断言（3.13 有 PYI-5670；3.14 不支持） |
| Ubuntu runner apt-get 偶发挂死（archive.ubuntu.com） | 基础设施偶发，`gh run rerun --failed` 即可；连续 2 次同点挂死才考虑 apt 镜像源/超时参数 |
| tessdata 下载 404/503/Connection reset | 多源 fallback（github 重定向→raw 直链→jsDelivr）+ curl `--retry 3 --retry-delay 2 --retry-all-errors` |
| e2e.yml paths 与 required check 不自洽 | paths 必须覆盖所有可能影响 E2E 的路径（constraints/**、.github/workflows/**），否则产生永远无法合并的 PR |
| 大文件下载 | 清华镜像 `python -m pip download -i https://pypi.tuna.tsinghua.edu.cn/simple` |
| gh run view --log-failed 看不到重定向吞掉的 stdout | `gh api repos/<owner>/<repo>/actions/runs/<id>/logs` 下载完整日志解压分析 |

---

## 8. 通用方法论（血泪教训）

1. **先诊断再动手**：下载完整日志 / 下载真实产物逐字节分析 / 解压 wheel 读 ELF
   动态段（SONAME/RPATH/RUNPATH/NEEDED），不要基于猜测连发构建——每轮 CI 1.5 小时。
2. **区分"加载成功"与"被找到"**：CDLL 预加载全部成功 ≠ paddle 能找到库
   （无 SONAME 时已加载列表匹配不命中）。机制验证要闭环到消费方。
3. **glibc 动态加载三定律**：
   - 裸名 dlopen 搜索序：已加载列表 → LD_LIBRARY_PATH → DT_RPATH/RUNPATH →
     ld.so.cache → 系统目录；
   - LD_LIBRARY_PATH 只在进程启动时解析一次；
   - RPATH/RUNPATH 只对"库加载其 NEEDED 依赖"生效，不参与裸名 dlopen。
4. **优先用上游官方机制**：paddle 的 FLAGS_*_dir 环境变量 > 自己造 preload/hook。
5. **manifest create/verify 两侧规则必须逐字节对称**（路径 resolve、排除规则、symlink）。
6. **跨平台脚本写最小公分母**：bash 3.2 兼容、BSD/GNU 工具差异（cp -f）、
   平台文件名差异（Chrome 可执行名）、`set -u` 空数组陷阱。
7. **本地能验证的先本地验证**（语法/单测/真实产物跑校验），再花 CI 轮次。
8. **同一错误连续 2 轮未解决就停下换思路**——v0.9.1 的 paddle 库加载连烧 5 轮
   弯路后才换到正解，代价过高。
9. **PyInstaller 版本敏感**：6.15（本项目锁定）的参数名/行为与 7.x 不同
   （include_py_files vs includes_py_files；6.16+ macOS BUNDLE 有变化）。

---

## 9. 附：v0.9.1 完整修复时间线（提交 → run → 结果）

| 阶段 | 提交 | 修复内容 |
|---|---|---|
| macOS 1-11 轮 | f2e50ba 等 16 个 | COLLECT+BUNDLE 结构、签名、bash3.2、dylib、Chromium 名 |
| selenium | 8245a9a/4c8e6e3/232c016 | BiDi 上游 bug → 三平台跳过 |
| Windows | 1e64abe | scipy 收集 + PDX 重初始化 |
| 改名配套 | 9275b7c→c430166 | CLI 改名 + integrity casefold |
| 36 轮 | 24bdca9 + b2fd059 | tessdata fallback + Linux duplicate 误报 |
| 37 轮 | 943f0e2 | Linux scipy 收集 |
| 38-43 轮 | 6239923→9804baf | paddle 库加载 5 轮弯路 → copy-to-root 生效 → mkldnn 禁用 |
| 44 轮 | 362e9ee | tar 深校验流式化（59min→3min） |
| 45 轮 | 49bdbf9 | cp -rL 解引用 |
| 46 轮 | 700cab0 | 供应链门禁三连修 |
| 47-48 轮 | bdde39a | FLAGS_*_dir 替代库复制（瘦身） |
| 49 轮 ✅ | 0043611 | tar.xz（2095→1714MB）→ **发布成功** |
