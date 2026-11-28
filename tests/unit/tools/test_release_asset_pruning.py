"""发布集收敛与合并校验清单（2026-11-28）。

## 缺陷

v1.2.0 的 Release 挂了 **20 个资产**，其中 9 个是同一件事的分平台残片：

| 资产 | 份数 | 对用户的意义 |
|---|---|---|
| `SHA256SUMS-<platform>.txt` | 3 | 用户只需要「我下载的这个包对不对」，不需要分平台三份 |
| `artifact-size-report-<platform>.json` | 3 | **CI 证据**，不是交付物 |
| `portable-smoke-<platform>.json` | 3 | **CI 证据**，不是交付物 |

## 修法

finalize 阶段**先把 CI 证据移出发布集**（仍留在 Actions artifacts），
再生成**一份**覆盖全部待发布资产的 `SHA256SUMS.txt`。

## 为什么顺序不能反

必须在**生成 provenance 之前**收敛 —— 否则 provenance 会把已经不发布的文件
也记进清单，等于「证明了一个没发出去的东西」。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[3]
FINALIZE = PROJECT_ROOT / ".github" / "workflows" / "reusable-finalize-release.yml"


def _steps() -> list[dict]:
    data = yaml.safe_load(FINALIZE.read_text(encoding="utf-8"))
    return data["jobs"]["finalize"]["steps"]


def _step_by_name(fragment: str) -> dict:
    for step in _steps():
        if fragment in (step.get("name") or ""):
            return step
    raise AssertionError(f"finalize 里没有名字含「{fragment}」的步骤")


def _names() -> list[str]:
    return [step.get("name") or "" for step in _steps()]


def test_ci_only_sidecars_are_moved_out_of_the_release_set() -> None:
    """★ 核心判据：size-report / smoke manifest 必须被移出发布集。

    这两类文件在 v1.2.0 里各占 3 个资产，却只是 CI 证据 ——
    用户下载页面被它们占满，真正该看的包要往下翻。
    """
    script = _step_by_name("收敛发布集")["run"]
    assert "artifact-size-report-*.json" in script, "size-report 没被移出发布集"
    assert "portable-smoke-*.json" in script, "smoke manifest 没被移出发布集"
    assert "mv " in script, "只是列出来不算移出，必须真的 mv 走"


def test_merged_checksums_replace_per_platform_ones() -> None:
    """★ 三份 `SHA256SUMS-<platform>.txt` 必须移出，并合并成一份 `SHA256SUMS.txt`。"""
    prune = _step_by_name("收敛发布集")["run"]
    assert "SHA256SUMS-*.txt" in prune, "分平台校验和没有被移出"

    merged = _step_by_name("生成合并校验清单")["run"]
    assert "--output SHA256SUMS.txt" in merged, "合并后的清单没有生成"


def test_checksum_ordering_around_provenance() -> None:
    """★ 顺序判据（两个方向都要）：

    ① 收敛必须在 provenance **之前** —— 否则 provenance 会把已不发布的文件
       也记进清单，「证明了一个没发出去的东西」；
    ② 合并清单必须在 provenance **之后** —— 否则清单覆盖不到 provenance，
       证明链缺一环，而且是静默的。
    """
    names = _names()
    prune = next(i for i, n in enumerate(names) if "收敛发布集" in n)
    provenance = next(i for i, n in enumerate(names) if "生成 provenance" in n)
    merged = next(i for i, n in enumerate(names) if "生成合并校验清单" in n)
    assert prune < provenance, f"收敛（{prune}）必须在 provenance（{provenance}）之前"
    assert provenance < merged, f"合并清单（{merged}）必须在 provenance（{provenance}）之后"


def test_prune_happens_before_checksums_are_signed() -> None:
    """★ 签名必须在合并之后：签的必须是**最终发布的那份**清单。"""
    names = _names()
    prune = next(i for i, n in enumerate(names) if "收敛发布集" in n)
    signing = next(i for i, n in enumerate(names) if "GPG 签名" in n)
    assert prune < signing


def test_gpg_signing_glob_covers_the_merged_name() -> None:
    """★ 签名 glob 必须能命中合并后的 `SHA256SUMS.txt`。

    修前写的是 `SHA256SUMS-*.txt`（带横杠）—— 合并成 `SHA256SUMS.txt` 之后
    这个 glob **一个都匹配不到**，于是 GPG「配了却没生效」，
    而状态会如实登记成 signed=false（不算撒谎，但等于白配）。
    """
    script = _step_by_name("GPG 签名")["run"]
    globs = re.findall(r"\$ARTIFACTS\"/SHA256SUMS[^\s\"]*", script)
    assert globs, "签名步骤里找不到 SHA256SUMS glob"
    for glob in globs:
        assert "-*" not in glob, f"glob {glob} 匹配不到合并后的 SHA256SUMS.txt"


def test_sig_signed_is_defined_before_first_use() -> None:
    """★ `set -euo pipefail` 下引用未定义变量直接退出。

    修前 `SIG_SIGNED` 在「安全」小节**末尾**才求值，而小节开头就用它 ——
    发布说明根本生成不出来，整个发布中止在最后一步。
    """
    script = _step_by_name("组装发布说明")["run"]
    # ★ 必须先剥掉注释行：本步骤的注释里就写着 `if [ "$SIG_SIGNED" = "yes" ]`
    #   作为说明文字，按原文匹配会把它当成"一次使用"，判据直接失真。
    code_lines = [
        line for line in script.splitlines()
        if not line.strip().startswith("#")
    ]
    code = "\n".join(code_lines)
    definitions = [m.start() for m in re.finditer(r'^\s*SIG_SIGNED="', code, re.MULTILINE)]
    uses = [m.start() for m in re.finditer(r'if \[ "\$?\{?SIG_SIGNED\}?" = "yes" \]', code)]
    assert definitions, "SIG_SIGNED 没有定义"
    assert uses, "找不到对 SIG_SIGNED 的使用"
    assert min(definitions) < min(uses), "SIG_SIGNED 在定义之前就被使用了（set -u 会直接失败）"


def test_sig_signed_defines_exactly_once() -> None:
    """★ 只定义一次。重复赋值说明编辑时留下了死代码。"""
    script = _step_by_name("组装发布说明")["run"]
    definitions = re.findall(r'^\s*SIG_SIGNED="', script, re.MULTILINE)
    assert len(definitions) == 1, f"SIG_SIGNED 被赋值了 {len(definitions)} 次"


def test_release_body_no_longer_promises_per_platform_checksums() -> None:
    """★ 发布说明不能提已经不存在的 `SHA256SUMS-platform.txt`。"""
    script = _step_by_name("组装发布说明")["run"]
    # 注释里提到历史形态是允许的；只查真正会被 echo 出去的内容
    echoed = "\n".join(
        line for line in script.splitlines()
        if "echo " in line and not line.strip().startswith("#")
    )
    assert "SHA256SUMS-platform.txt" not in echoed, "发布说明仍在承诺分平台校验和"
    assert "SHA256SUMS.txt" in echoed, "发布说明没有提到合并后的清单"


def test_skipped_manifests_never_enter_the_checksum_manifest(tmp_path: Path) -> None:
    """★ `SKIPPED-<platform>.txt` 是记账，不是可下载资产。

    它在 finalize 阶段**留在**发布目录（后面「组装发布说明」与「缺席汇总告警」
    两步都要读它），所以生成合并清单时必须主动排除 —— 否则用户会照着哈希一个
    自己根本下不到的文件。
    """
    import subprocess
    import sys


    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    for name in (
        "OmniCrawler-9.9.9-Windows-Portable-Standard.zip",
        "OmniCrawler-9.9.9-Linux-Portable-Standard.tar.xz",
        "OmniCrawler-9.9.9-macOS-Portable-Standard.dmg",
        "omnicrawler-sbom-linux.cdx.json",
    ):
        (artifacts / name).write_bytes(b"x")
    (artifacts / "SKIPPED-linux.txt").write_text("Full 超限\n", encoding="utf-8")

    script = PROJECT_ROOT / "tools" / "releasing" / "generate_checksums.py"
    result = subprocess.run(
        [sys.executable, str(script), str(artifacts), "--output", "SHA256SUMS.txt"],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    manifest = (artifacts / "SHA256SUMS.txt").read_text(encoding="utf-8")
    assert "SKIPPED" not in manifest, "校验清单误收了 SKIPPED 记账文件"
    assert "Linux-Portable-Standard.tar.xz" in manifest, "清单漏了真实产物"

def test_public_key_is_committed_and_documented() -> None:
    """★ 公钥必须入库，且指纹必须出现在签名文档里。

    只把私钥配进 CI 而公钥不入库，用户拿到 `.sig` 也无法验证 ——
    「有签名但你验不了」和「没签名」对用户是一样的。
    """
    key = PROJECT_ROOT / "keys" / "release-signing.pub.asc"
    assert key.is_file(), "keys/release-signing.pub.asc 不存在 —— 用户无法验证 .sig"
    text = key.read_text(encoding="utf-8")
    assert "BEGIN PGP PUBLIC KEY BLOCK" in text
    assert "BEGIN PGP PRIVATE KEY" not in text, "私钥绝不能入库"

    doc = (PROJECT_ROOT / "docs" / "SIGNING.md").read_text(encoding="utf-8")
    # 指纹必须逐字出现在文档里，否则用户没有可比对的锚点
    fingerprint = "601E 4918 0F6C F626 5EFC  D4BD 7E22 F722 7C4D 9995"
    assert fingerprint in doc, "文档里没有可核对的公钥指纹"
    assert "SHA256SUMS.txt.sig" in doc, "文档没有说明签名文件叫什么"


def test_signing_doc_states_windows_is_unsigned() -> None:
    """★ 未签名必须**写明**，不能靠用户自己撞上 SmartScreen 才发现。"""
    doc = (PROJECT_ROOT / "docs" / "SIGNING.md").read_text(encoding="utf-8")
    assert "未签名" in doc, "文档没有说明 Windows 未签名"
    assert "SmartScreen" in doc, "没有提示用户会遇到什么"


def test_signing_doc_does_not_promise_paid_signing() -> None:
    """★ 文档不得声称已做 Authenticode / Developer ID（当前确实没做）。"""
    doc = (PROJECT_ROOT / "docs" / "SIGNING.md").read_text(encoding="utf-8")
    for claim in ("已做 Authenticode 签名", "已完成 Apple 公证", "已完成 Developer ID 签名"):
        assert claim not in doc, f"文档声称了未做的签名：{claim}"
