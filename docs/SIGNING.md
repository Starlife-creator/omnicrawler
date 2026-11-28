# 发布签名

> 适用版本：**1.2.0** · 配置协议：v5 · 维护状态：现行

本文说明 OmniCrawler 发布产物的签名现状、如何自己验证，以及维护者如何配置签名。

## TL;DR

| 平台 | 签名方式 | 状态 | 需要付费吗 |
|---|---|---|---|
| **Linux / 通用** | GPG detached signature（`SHA256SUMS.txt.sig`）| ✅ 已启用 | 否 |
| **Windows** | Authenticode | ❌ 未签名（个人项目，无预算）| 是 |
| **macOS** | Developer ID + 公证 | ❌ 仅 ad-hoc | 是 |

**如实声明**：Windows 便携包会触发 SmartScreen「未知发布者」提示；macOS 首次打开需要
右键 → 打开。这不是缺陷隐瞒，是**当前没有付费证书**的客观结果，写在这里而不是让用户
自己去猜。

## 如何验证你下载的产物

### 1. 核对 SHA-256（不需要任何工具以外的信任）

```bash
# Linux / macOS
sha256sum -c --ignore-missing SHA256SUMS.txt
```

```powershell
# Windows PowerShell
Get-FileHash .\OmniCrawler-1.2.0-Windows-Portable-Standard.zip -Algorithm SHA256
# 与 SHA256SUMS.txt 里对应那行比对
```

`SHA256SUMS.txt` 覆盖**全部**发布资产（三个便携包 + SBOM + provenance +
签名状态 + 更新清单）。清单里每一行都有对应文件。

### 2. 验证 GPG 签名（确认清单本身没被篡改）

SHA-256 只能证明「文件与清单一致」；它不能证明**清单本身**是发布者发的。
GPG 签名补上这一环。

**第一步：取公钥。** 仓库内 `keys/release-signing.pub.asc`，可直接从
`https://github.com/Starlife-creator/omnicrawler/raw/main/keys/release-signing.pub.asc` 下载。

指纹（务必逐字核对）：

```
601E 4918 0F6C F626 5EFC  D4BD 7E22 F722 7C4D 9995
```

子密钥（实际签名密钥）：

```
D367 F300 2ED2 94B8 2290  34D5 C97F 31D8 944F 3FCA
```

**第二步：导入并验签。**

```bash
gpg --import release-signing.pub.asc
gpg --verify SHA256SUMS.txt.sig SHA256SUMS.txt
```

看到 `Good signature from "Starlife-creator (OmniCrawler release signing)"` 即通过。

> `WARNING: This key is not certified with a trusted signature!` 是**正常的**（新导入的
> 公钥没有本地信任路径）。关键是 `Good signature` —— 并且**指纹与上面一致**。
> 只看 `Good signature` 而不核对指纹，等于信任了「任意一把自称是发布者的钥匙」。

### 3. 验证 SLSA 构建来源（不需要导入任何密钥）

```bash
gh attestation verify OmniCrawler-1.2.0-Linux-Portable-Standard.tar.xz --owner Starlife-creator
```

这条证明「这个文件确实由本仓库的 GitHub Actions 在指定提交上构建」。

## 三层关系

```
SLSA 证明 ──► 证明「哪个提交、在哪构建的」（由 GitHub 的 OIDC 背书）
     │
     ├─ SHA256SUMS.txt ──► 证明「这堆文件是一起发布的」
     │
     └─ .sig ──► 证明「这份清单是持有私钥的人发的」
```

三者互补，不能相互替代。

## 维护者：重新配置 GPG 签名

签名在 `.github/workflows/reusable-finalize-release.yml` 的「登记签名状态并
条件执行 GPG 签名」步骤中完成 —— **只在仓库配置了 `GPG_PRIVATE_KEY` secret 时执行**，
没配就如实登记为未签名。

### 生成密钥

```bash
gpg --full-generate-key
# 选 RSA 4096、用途 sign、有效期按需
```

### 导出并上传

```bash
gpg --list-secret-keys --keyid-format LONG      # 拿到 KEY_ID
gpg --armor --export-secret-keys $KEY_ID > private.asc
gpg --armor --export $KEY_ID > keys/release-signing.pub.asc   # 公钥入库

gh secret set GPG_PRIVATE_KEY < private.asc
# 若密钥有 passphrase，再设一个：
gh secret set GPG_PASSPHRASE

rm private.asc   # 不要留在磁盘上
```

### 为什么用「无 passphrase 的专用签名密钥」

CI 需要无人值守地签名。把 passphrase 也塞进 secret 只是把它换个地方存，
并没有真的增加安全性。**更好的做法是承认这把钥匙只用于发布签名**：

- 它的唯一权限是签发布清单，不签插件、不签组件（那是另外两把钥匙）；
- 有效期设 3 年，到期必须主动轮换；
- 一旦泄露，作废方式是**发布一份新公钥并声明旧指纹失效** —— 因此指纹必须
  出现在公开文档里（就是本文），旧指纹被替换这件事本身可被审计。

## 为什么 Windows 不签名

2023 年 6 月起，CA/Browser Forum 要求所有代码签名证书（OV 与 EV 都算）必须存放在
FIPS 140-2 Level 2 及以上的**硬件令牌或 HSM** 中 —— 「买一张证书下载 .pfx」已不可行。

这意味着：

| 方案 | 大致成本 | 对个人项目的问题 |
|---|---|---|
| OV 证书 | 约 ¥1500–3000/年 | 需要企业身份审核 + 硬件令牌；且仍会触发 SmartScreen |
| EV 证书 | 约 ¥2500–4500/年 | 同上，但立即获得 SmartScreen 信任 |
| Azure Trusted Signing | 按月计费 | 需要**可验证的企业历史** |
| SignPath Foundation | 开源项目免费 | 需满足其开源项目资格审核 |

**本项目的选择：不签名，但把不签名这件事写清楚。** 完整性由
SHA-256 + GPG 签名清单 + SLSA attestation 三层保证，这三层已经覆盖了
「文件是不是发布者发的、有没有被改过」这两个真正的问题。

> 顺带一提：`build_windows.ps1` 里的 `-CodeSigningThumbprint` 分支仍然保留
> （本地构建可用），但它按指纹从**本机证书存储**取证书 —— 硬件令牌方案下
> CI 用不了这条路。将来若接入 Azure Trusted Signing，需要改这段的调用方式，
> 而不是只补一个 secret。

## macOS 的现状

`build_macos.sh` 做 **ad-hoc 签名**（`codesign --sign -`），这能让 app bundle
内部结构自洽、Gatekeeper 不直接拒绝，但**不建立任何发布者身份**。

真正的 Developer ID 签名 + 公证需要 Apple Developer Program（$99/年），同样未采用。

## 验证判据（给维护者）

发布后自查：

```bash
# 1. 签名状态如实登记
gh release download v<版本> --pattern SIGNING-STATUS.json --dir /tmp/chk
cat /tmp/chk/SIGNING-STATUS.json   # linux_gpg.signed 应为 true

# 2. .sig 真的存在且可验
gh release download v<版本> --pattern "SHA256SUMS*" --dir /tmp/chk
gpg --verify /tmp/chk/SHA256SUMS.txt.sig /tmp/chk/SHA256SUMS.txt
```

`tests/unit/tools/test_release_asset_pruning.py` 钉住了三件事：

- 分平台校验和必须被合并（不能退回三份）；
- 签名 glob 必须能命中合并后的 `SHA256SUMS.txt`（`SHA256SUMS-*.txt` 会**一个都匹配不到**，
  等于「配了却没生效」）；
- 收敛必须在 provenance 之前、合并清单必须在 provenance 之后。
