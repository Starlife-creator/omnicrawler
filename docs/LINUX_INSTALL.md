# Linux 用户级安装（应用菜单条目与图标）

> 适用版本：0.13.1 · 配置协议：v5 · 维护状态：现行

Linux 便携包**解压即可运行**，不需要任何安装步骤。本文讲的是可选的第二步：
如果你希望 OmniCrawler 出现在**应用菜单**里、带上正确的图标，并能干净卸载 ——
包内自带一个**用户级**安装脚本。

全程**不需要 root**，也**不会**写任何系统目录。

## 快速开始

```bash
tar -xJf OmniCrawler-<版本>-Linux-Portable-Full.tar.xz
cd OmniCrawler
./installer/install-user.sh
```

安装完成后：应用菜单里搜索 `OmniCrawler` 即可启动；命令行则是
`~/.local/bin/omnicrawler`。

## 它做了什么

| 动作 | 落点 |
|---|---|
| 把整棵应用树**搬进**前缀 | `~/.local/opt/OmniCrawler`（可用 `--prefix` 改） |
| 生成桌面条目 | `~/.local/share/applications/omnicrawler.desktop` |
| 安装 7 个尺寸的图标 | `~/.local/share/icons/hicolor/<N>x<N>/apps/omnicrawler.png` |
| 命令行软链 | `~/.local/bin/omnicrawler` |
| 刷新桌面数据库 | `update-desktop-database`（未安装则跳过并提示） |

★ **为什么是"搬进前缀"而不是"就地注册"**：如果桌面条目的 `Exec=` 指向你解压的位置
（多半是 `~/Downloads`），你哪天清理下载目录，应用就失效并留下一个死链。所以安装会把
应用树复制到前缀，再按**最终绝对路径**生成条目。

选项：

| 选项 | 作用 |
|---|---|
| `--prefix <目录>` | 安装前缀，默认 `~/.local/opt/OmniCrawler` |
| `--move` | 安装成功后**询问**是否删除源目录（默认保留；只有输入 `yes` 才删） |
| `--no-desktop-database` | 不调用 `update-desktop-database` |
| `--quiet` | 少打印 |

重复执行是**幂等**的：已经在前缀内运行时跳过复制，不报错也不叠加。

## 前置依赖

GUI 需要 13 个 Qt/X11 运行库与一套中文字体（与 CI 的构建/冒烟环境同一清单）。
脚本**只检测并报告**缺什么、给出对应的 `apt-get` 命令，**不会**自动安装 ——
那需要 root，而本脚本刻意不碰系统域。

```bash
sudo apt-get install -y --no-install-recommends \
  libegl1 libxcb-icccm4 libxcb-keysyms1 libxcb-image0 libxcb-shape0 \
  libxcb-render-util0 libxcb-xkb1 libxkbcommon-x11-0 libxcb-cursor0 \
  libxcb-xinerama0 libxcb-xfixes0 libxcb-randr0 libxcb-glx0 fonts-noto-cjk
```

Tesseract 与 OCR 语言包同为系统依赖，见 [安装、运行与平台矩阵](INSTALLATION.md)。

## 卸载（★ 数据安全）

```bash
~/.local/opt/OmniCrawler/installer/uninstall-user.sh
```

默认**只摘**安装脚本自己生成的三样（桌面条目、那个图标文件、CLI 软链），
**不动**应用树。

原因是一个真陷阱：便携模式下**数据根就在应用目录内**（`PORTABLE.flag` /
`work/` `data/` `output/` `logs/`）。"删掉整个应用目录"会连同你的抓取结果一起删掉。
因此：

| 命令 | 行为 |
|---|---|
| `uninstall-user.sh` | 只摘条目 / 图标 / 软链，保留应用树与数据 |
| `uninstall-user.sh --remove-prefix` | 连应用树一起删；**检测到数据在里面就拒绝**（非零退出）并打印数据位置 |
| `uninstall-user.sh --purge-data` | 显式同意后才连数据一起删（**不可逆**） |
| `uninstall-user.sh --prefix <目录>` | 指定前缀；不指定时会从已装的 `.desktop` 的 `Exec=` 反推 |

脚本**绝不**触碰 `~/.omnicrawler/`（凭据/热密钥），也不会去删任何外部数据根。

## 为什么没有 deb / rpm / AppImage

* **deb/rpm** 属于系统域、需要提权，而且包管理器"卸载即清目录"的语义对本项目的
  数据根是灾难（见上一节）。
* **不做 Windows 安装器**同理：三平台都没有代码签名，无签名的安装器首启体验
  （SmartScreen 拦截、UAC）比"解压即用"更差。
* macOS 不提供任何安装形态：拖进 `/Applications` 会丢掉包内的 `browsers/`，
  导致动态网页能力失效。

Windows 侧对应的是**首启可选的**桌面/开始菜单快捷方式（默认**不勾**，需显式同意），
见 [便携版构建](PORTABLE_PACKAGING.md)。

## 验证安装是否真的对

三条最小检查：

```bash
grep '^Exec=' ~/.local/share/applications/omnicrawler.desktop   # 必须是绝对路径
ls ~/.local/share/icons/hicolor/*/apps/omnicrawler.png          # 应有 7 个尺寸
command -v desktop-file-validate >/dev/null && \
  desktop-file-validate ~/.local/share/applications/omnicrawler.desktop
```

装配层面还有两道机器判据兜底：便携包完整性检查要求 `installer/` 下的脚本与
7 个图标都在归档里；归档冒烟会在解压后的**真产物**上跑一遍安装，并断言
`Exec=` 指向的入口真的存在。
