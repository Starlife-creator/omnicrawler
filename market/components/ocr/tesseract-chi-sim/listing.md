# Tesseract 中文简体语言包

给 Tesseract OCR 引擎补上中文简体识别能力。

## 这是什么

一份 **recipe 声明**——告诉宿主「从哪拉、哈希是多少、怎么拼」，本身不含词库字节。

| 项 | 值 |
|---|---|
| 上游来源 | `github.com/tesseract-ocr/tessdata_fast` |
| 构件大小 | 约 2.4 MB（`chi_sim.traineddata`） |
| 本包大小 | **750 字节**（只有声明） |
| 引擎 | Tesseract |

## 为什么这么小

维护者定的分工是「**下载从上游拉，需要组装就在本地组装，本项目只维护组装方式**」。

所以：

- 市场仓**不托管**词库字节（没有我们的 CDN）；
- 下载走宿主的受控出站（`EgressBroker`），带审计与预算；
- 每个构件**必须**带 sha256，缺一条就拒绝整份 recipe（不做部分信任）。

这样仓库不会被几百 MB 的模型撑爆，也避免了我们为第三方字节背书。

## 安装

```bash
# 先只读预览：拉到哪些 URL、多大、什么哈希
omnicrawler components inspect-recipe --recipe recipe.json

# 真正下载并装配（必须显式加 --download）
omnicrawler components install-recipe --recipe recipe.json --download
```

不带 `--download` 时**一个字节都不下载**，只做声明校验并打印计划。

## 安全说明

- recipe 是**数据，不是程序**：含 `post_install` 之类的可执行钩子会被直接拒绝；
- 所有路径经规范化，`..` 穿越会被拒；
- 装配是两阶段的：先全部校验，再原子替换——失败不留半成品。
