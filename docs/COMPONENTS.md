# 可选组件管理

> 适用版本：1.0.20 · 配置协议：v5 · 维护状态：现行

## 两条安装通道（2026-11-19 起）

| 通道 | 字节来源 | 适用 |
|---|---|---|
| **市场 recipe 装配** | 上游 URL（PyPI / GitHub / 模型源） | 市场里的组件条目；本仓只托管几十 KB 的声明 |
| **离线整包 `.ocp`** | 用户手工下载的本地文件 | 离线 / 内网分发 |

**市场只托管声明，不托管字节**：`recipe.json` 列出「从哪个 URL 拉、sha256 是多少、
怎么拼」，不存模型或二进制。重型运行时（`paddle` 104 MB）与语言包（Tesseract
`.traineddata`）都走这条；模型权重更走上游直拉（见下）。

```bash
omnicrawler components inspect-recipe --recipe recipe.json   # 只读：看要拉什么
omnicrawler components install-recipe --recipe recipe.json  # 默认**不下载**，只展示计划
omnicrawler components install-recipe --recipe recipe.json --download   # 实际下载并装配
```

不加 `--download` 时**一个字节都不下载**——从上游拉字节是显式对外动作。

## Wheel-Pack 组件在**隔离子进程**里运行

`entry.runner: worker-wheelpack` 的组件由独立 Python 子进程执行：

```bash
omnicrawler components run --component-dir <已装配组件> [--payload '{...}'|@file]
```

- **为什么子进程**：`paddle` / `cv2` / `scipy` 与宿主已加载的 `PySide6` / `lxml`
  同进程时极易触发原生库符号冲突；Linux 下部分库还要求进程启动前设环境变量。
- **启动前二次校验**：按 `component.json` 重算每个文件哈希。组件目录可能在装配后
  被改动，子进程一旦起来就没有拦截机会——**不信任「上次校验过」**。
- **零 pip、零安装脚本**：`.whl` 是纯数据 ZIP，装配时已校验 SHA-256；运行器只挂
  `sys.path`，不做任何安装动作。

## 模型绝不进 recipe

`paddle_models` 等大模型权重**严禁**出现在 `recipe.json` 里——上游不提供模型文件的
内容哈希，伪造一个就是自欺。模型继续走 `services/model_download.py` 的四源直拉
（AIStudio / ModelScope / BOS / HuggingFace），信任锚点归属上游平台。

## 历史沿革

当前实现支持离线组件包管理与 OCR 独立运行时协议 Preview。2026-10-04 已完成 Windows 本机 Tesseract 独立组件试点：实际冻结入口、原生模型/运行库、隔离开发信任目录、真实 OCR 真值和字节篡改拒绝；完整主程序冻结产物、干净机器、跨平台和浏览器组件仍待验收。现有 Standard/Full 离线套装继续保留。

正常安装会验证组件清单签名及清单内文件的 SHA-256。将维护者明确认可的 Ed25519 公钥保存为便携数据根目录下 `.omnicrawler/component_trust.pub.pem`；不存在信任公钥时默认拒绝安装。替换此文件可切换信任根，已移除的旧密钥签名不再被接受。不要从待安装包中自动导入公钥。`--allow-unsigned` 仅用于本地开发测试。

安装、卸载和回滚共用组件根目录下的跨进程注册表锁，锁等待超时会明确失败。版本目录不可变；卸载移除注册表记录并保留版本字节，供回滚及持有该版本的任务使用。当前不提供自动清理版本目录。

注册表使用相对路径，整个组件目录搬迁后仍可读取。升级及卸载保存旧版本完整元数据；回滚验证旧文件哈希后恢复旧条目。清单提交有持久标记，进程中断后下一次受锁访问会重放提交；损坏的清单显式报错，不被空清单静默覆盖。

组件包的 `component.json` 包含 `name`、`version`、`files` 和可选 `dependencies`。名称和版本必须是安全的单级标识；文件必须使用相对 POSIX 路径。依赖兼容原有名称列表，也支持 `runtime>=1.0,<2`。版本约束支持数字版本与 `== != >= <= > <`，未知语法拒绝；不将预发行版本近似为正式版。

可选 `core_version` 使用同样的约束；`platforms` 可声明 `win32` / `linux` / `darwin`；`architectures` 使用 `x86_64` / `arm64` 等平台架构名。安装与回滚校验当前环境、整个已安装集合的依赖及循环依赖，卸载识别带版本约束的反向依赖。

文件先在私有目录校验解压，再原子激活版本目录。激活后、提交清单前中断时，可重新导入同一受信包；保留目录只有在文件集合与哈希完全一致时才可复用。注册表与旧版元数据通过同一持久提交日志恢复。不应把成功导入当作冻结 OCR 已验收。

OCR 包新增受签名保护的 `runtime` 声明，例如：`{protocol: ocr-file-v1, engine: paddle, entrypoint: runtime/ocr.exe, model_license: Apache-2.0}`。入口、运行库及全部离线模型须在 files 中，平台/架构、核心范围和依赖必须准确声明。版本目录不得写缓存；运行入口不接受 Python/批处理脚本，也不把 wheel 动态加入主程序搜索路径。原始签名清单保留在组件根目录 `.manifests/<name>/<version>.json`；旧组件没有此清单时须重新导入受信包才能执行。运行时重验当前信任根、签名、完整文件集合、兼容与每个哈希，密钥撤销对后续执行生效。

主任务可明确配置 `processors.pdf: {enabled: true, ocr_backend: paddle, ocr_component: ocr-paddle}`；独立 PDF 项目配置 `ocr: {backend: paddle, component: ocr-paddle}`。使用自定义/已生成 PDF 项目配置时该项目本身是 OCR 设置真源，需同时更新它。未指定组件时保留原 OCR 路径。预检报告缺失、损坏、协议或兼容问题。

宿主调用固定命令 `ocr.exe --request <request.json> --result <result.json> --offline`。请求格式 1 包含 engine/lang、同临时目录 input.png 与 input_sha256；结果格式 1 必须回传该摘要、text 和可选 0–1 confidence。只接受受限 PNG 和受限结果。子进程仅继承系统初始化路径，禁止继承凭据、代理与 Python 路径；超时由宿主回收。受信运行时必须遵守离线契约并自行携带模型/运行库；此协议不是通用插件执行权限。

执行期间持有注册表事务租约，安装/卸载等待资源退出或明确超时；首版串行化组件执行，不新增常驻服务。初始化固定版本，中途升级要求重新开始任务。DLL/ABI、冻结模块发现、干净机器断网、搬迁、实际 OCR 真值与总体成本仍须 CI/实机验证；没有可信实际产物前不得发布成轻量正式版。

## 信任边界：哪些东西被校验，哪些不被校验

本项目分发三类资产，完整性责任**刻意不同**。这张表是唯一准据
（代码侧对应 `src/omnicrawler/services/asset_trust.py` 的 `HASH_RESPONSIBILITY`，
并由 `tests/unit/services/test_asset_trust_boundary.py` 守卫）：

| 资产 | 来源 | 有内容哈希 | 谁负责完整性 |
|---|---|---|---|
| 应用本体（便携包） | 本项目构建 | **有** | 本项目（发布签名 + 逐文件 sha256） |
| 组件包（`.ocp`） | 可为第三方，经签名 | **有** | 签名者 + 本项目验签 |
| Paddle OCR 模型 | **上游**（PaddleX 自行下载） | **没有** | 上游 |

**关于 Paddle 模型，必须说清楚的一点**：

> 本项目**不校验模型字节**，也不为其完整性背书。模型由 PaddleX 按模型名
> 从 BOS / HuggingFace / ModelScope 等上游下载到本地缓存，本项目不托管、不镜像。

之所以不做，是因为**上游不提供可用的内容哈希**：
`paddlex/inference/utils/official_models.py` 里出现的那串 sha256 是用
**模型名列表**算出来的缓存键，**不是**文件内容的校验值。拿它当校验值，
换掉模型内容后它**不会变**——那会让界面和日志看起来"已校验"，
比明确写"未校验"危险得多。

因此这里有一条硬约定：**没有内容哈希的资产，绝不伪造哈希**。
不许"拿模型名 sha256 一下顶上"，也不许把模型塞进要求逐文件哈希的
组件清单里（那会让清单里的哈希变成假值）。

反之，**组件包的哈希要求不因 Paddle 模型没有哈希而放宽**：组件构件由清单
声明来源，必须能证明"这份清单没被改过"，否则 ed25519 签名形同虚设。
两者是不同性质的东西，不能互相折中。

## 本机 Tesseract 试点（2026-10-04）

开发目录 `market-plugin-and-template-development/comprehensive-20261004/plugins/ocr-tesseract-runtime` 保存入口源码、离线模型和原生 DLL；开发包使用临时测试签名，公钥与证据保存在独立验收目录，不进入正式市场信任根。真实扫描/低质扫描经组件完成 ingest→parse→ocr→extract→export，输入摘要核对，篡改安装入口后在执行前拒绝。

试点压缩包 45,891,005 字节，安装文件合计 97,223,061 字节。相同渲染 PNG、各一次新进程的总进程树 RSS 测量：直接路径约 202.82 MiB，组件约 205.95 MiB；没有证据表明总体 OCR 更省内存。此结果证明组件交付协议可执行，不证明 Standard/Full 应用已减重。完整原生 DLL 再分发归属、干净机器断网及发布冻结矩阵完成前保持开发试点。详细证据见 [全面优化记录](COMPREHENSIVE_OPTIMIZATION_2026-10-04.md)。
