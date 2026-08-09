# `plugins/` —— 用户插件工作目录

这是**你自己的插件目录**，不是示例、也不是市场快照。

## 用途
- 把你开发的插件 `.py` 放在这里（可再分子目录，加载器会递归发现）。
- 启动时会自动加载本目录下所有已签名的插件（fail-closed：未签名/签名不符的会被拒绝并记入 `plugin_errors`）。
- 默认 `plugins.paths` 已包含本目录，无需额外配置。

## 与另外两个目录的区别
| 目录 | 角色 | 谁维护 |
|---|---|---|
| `examples/plugins/` | 官方示例/起步模板（含 `example_news`） | 项目维护者 |
| `plugins/`（本目录） | **你自己的插件** | 你 |
| `plugins_installed/` | `market.py install` 从市场下载验签后落盘处 | 安装命令 |
| `registry/` | 策展式市场发布快照（供 `market.py` 拉取） | 审核后发布 |

## 开发流程
1. 把 `examples/plugins/example_site.py` 复制成本目录下的新文件作为起点；
2. 实现你的 `register(registry)` 逻辑；
3. 用冷私钥签名：
   ```bash
   python tools/sign_plugin.py sign plugins/your_plugin.py \
       --key /path/to/cold-storage/plugin_signing_private.pem
   ```
4. 启动即自动加载；若要发布到市场，再走 `registry/` 的发布流程（复制+签名+`listing.md`）。

> 注意：本目录下的插件**必须签名**才能被加载。未签名文件会触发 fail-closed 拒绝。
> 不要把私钥放进本目录，私钥只存冷存储（见 `configs/` 与 `CONTRIBUTING.md`）。
