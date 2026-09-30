"""变更包产出端：一次构建 + K 份旧清单 ⇒ K 个变更包（`build_update_delta.py`）。

为什么要这条工具（此前**只有消费端、没有产出端**）：`update.json` 的 `payload.delta` 只是
声明"某个旧版本有对应变更包"，没有任何东西会产出那个包 ⇒ 客户端每次都退化成全量下载，
"小版本只发增量"这条路径永远走不到。

本文件钉四件事：① 只装"新增的 + 哈希不同的"成员；② **一次构建出 K 个包**（各基线差集不同）；
③ 成员带**权限位**且产物**可复现**（固定时间戳）；④ 与客户端对得上（端到端装成）。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

REPO_ROOT = Path(__file__).resolve().parents[3]
DELTA_TOOL = REPO_ROOT / "tools" / "build_update_delta.py"
BUILD_TOOL = REPO_ROOT / "tools" / "build_update_manifest.py"

sys.path.insert(0, str(REPO_ROOT / "src"))

from omnicrawler.plugins import signing  # noqa: E402
from omnicrawler.services.update_feed import (  # noqa: E402
    canonical_feed_bytes,
    decode_public_key,
    verify_feed_document,
)


def _run(tool: Path, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(tool), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=cwd or REPO_ROOT,
    )


def _portable_zip(path: Path, files: dict[str, bytes], *, modes: dict[str, int] | None = None) -> Path:
    """造一个便携包形态的 zip（顶层 ``OmniCrawler/``，可指定成员权限位）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as handle:
        for name, body in sorted(files.items()):
            info = zipfile.ZipInfo(f"OmniCrawler/{name}", date_time=(2020, 1, 1, 0, 0, 0))
            mode = (modes or {}).get(name, 0o644)
            info.external_attr = mode << 16
            handle.writestr(info, body)
    return path


def _signed_manifest(
    path: Path,
    private,
    *,
    version: str,
    files: dict[str, bytes],
    platform: str = "linux",
    edition: str = "standard",
) -> Path:
    """写一份**已签名**清单（只看 payload.files 的哈希/体积 ⇒ 不需要真实载荷）。

    ``files`` 为空 ⇒ **整个 `payload` 段都不写**：这正是"assets-only 清单"的形态
    （客户端要求 `payload.files` 非空，所以不能写成 `payload: {files: {}}`）。
    """
    document: dict[str, object] = {
        "version": version,
        "platform": platform,
        "edition": edition,
        "assets": {
            f"{platform}-{edition}": {"name": "pkg.tar.xz", "sha256": "a" * 64, "size": 1}
        },
    }
    if files:
        document["payload"] = {
            "files": {
                name: {"sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}
                for name, body in files.items()
            }
        }
    import base64

    signature = base64.b64encode(private.sign(canonical_feed_bytes(document))).decode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({**document, "signature": signature}, ensure_ascii=False), encoding="utf-8"
    )
    return path



def test_delta_contains_only_added_and_changed_members(tmp_path: Path) -> None:
    """① 变更包 = 新增的 + 哈希不同的；未变的一律不进。"""
    from cryptography.hazmat.primitives import serialization

    private_pem, public_pem = signing.generate_keypair()
    key = serialization.load_pem_private_key(private_pem, password=None)
    key_path = tmp_path / "update.pem"
    key_path.write_bytes(private_pem)
    trust = tmp_path / "trust.pem"
    trust.write_bytes(public_pem)

    archive = _portable_zip(
        tmp_path / "dist" / "OmniCrawler-0.15.3-Linux-Portable-Standard.zip",
        {"app": b"NEW", "same.so": b"UNCHANGED", "added.json": b"ADDED"},
        modes={"app": 0o755},
    )
    baseline = _signed_manifest(
        tmp_path / "prev.json", key, version="0.15.2",
        files={"app": b"OLD", "same.so": b"UNCHANGED", "removed.txt": b"GONE"},
    )
    out_dir = tmp_path / "delta"
    args_out = tmp_path / ".delta-args.txt"

    result = _run(
        DELTA_TOOL,
        "--payload-archive", str(archive),
        "--version", "0.15.3", "--platform", "linux", "--edition", "standard",
        "--baseline-manifest", f"0.15.2={baseline}",
        "--out-dir", str(out_dir),
        "--emit-args", str(args_out),
        "--trusted-public-key", str(trust),
    )
    assert result.returncode == 0, result.stdout + result.stderr

    package = out_dir / "update-0.15.2-to-0.15.3-linux-standard.zip"
    assert package.is_file()
    with zipfile.ZipFile(package) as handle:
        names = sorted(handle.namelist())
        assert names == ["added.json", "app"], "未变成员与已删除成员都不该进包"
        assert handle.read("app") == b"NEW"

    # 权限位必须带上（否则 Linux 上装完不可执行 —— 客户端现已会还原它）
    with zipfile.ZipFile(package) as handle:
        info = handle.getinfo("app")
        assert (info.external_attr >> 16) & 0o777 == 0o755
        data_info = handle.getinfo("added.json")
        assert (data_info.external_attr >> 16) & 0o777 == 0o644

    # 交给清单工具的 <旧版本>=<包路径> 行
    assert args_out.read_text(encoding="utf-8").strip().endswith(
        "update-0.15.2-to-0.15.3-linux-standard.zip"
    )


def test_one_build_produces_k_packages_for_k_baselines(tmp_path: Path) -> None:
    """② 一次构建 + K 份清单 ⇒ K 个包，且各自差集**不同**（这是 K 窗口的全部意义）。"""
    from cryptography.hazmat.primitives import serialization

    private_pem, public_pem = signing.generate_keypair()
    key = serialization.load_pem_private_key(private_pem, password=None)
    trust = tmp_path / "trust.pem"
    trust.write_bytes(public_pem)

    archive = _portable_zip(
        tmp_path / "dist" / "pkg.zip",
        {"app": b"V3", "b.dll": b"V3-B", "c.so": b"V3-C"},
    )
    near = _signed_manifest(
        tmp_path / "m3.json", key, version="0.15.2",
        files={"app": b"V2", "b.dll": b"V3-B", "c.so": b"V3-C"},
    )
    far = _signed_manifest(
        tmp_path / "m2.json", key, version="0.15.1",
        files={"app": b"V1", "b.dll": b"V1-B", "c.so": b"V3-C"},
    )
    out_dir = tmp_path / "delta"

    result = _run(
        DELTA_TOOL,
        "--payload-archive", str(archive),
        "--version", "0.15.3", "--platform", "linux", "--edition", "standard",
        "--baseline-manifest", f"0.15.2={near}",
        "--baseline-manifest", f"0.15.1={far}",
        "--out-dir", str(out_dir),
        "--trusted-public-key", str(trust),
    )
    assert result.returncode == 0, result.stdout + result.stderr

    with zipfile.ZipFile(out_dir / "update-0.15.2-to-0.15.3-linux-standard.zip") as handle:
        assert sorted(handle.namelist()) == ["app"]
    with zipfile.ZipFile(out_dir / "update-0.15.1-to-0.15.3-linux-standard.zip") as handle:
        assert sorted(handle.namelist()) == ["app", "b.dll"]


def test_delta_is_byte_reproducible(tmp_path: Path) -> None:
    """③ 同样输入必须产出**同样字节**（成员时间戳固定）——否则"发布产物可复现"失效。"""
    from cryptography.hazmat.primitives import serialization

    private_pem, public_pem = signing.generate_keypair()
    key = serialization.load_pem_private_key(private_pem, password=None)
    trust = tmp_path / "trust.pem"
    trust.write_bytes(public_pem)
    archive = _portable_zip(tmp_path / "dist" / "pkg.zip", {"app": b"NEW"})
    baseline = _signed_manifest(tmp_path / "m.json", key, version="0.15.2", files={"app": b"OLD"})

    digests = []
    for attempt in ("run1", "run2"):
        out_dir = tmp_path / attempt
        result = _run(
            DELTA_TOOL,
            "--payload-archive", str(archive),
            "--version", "0.15.3", "--platform", "linux", "--edition", "standard",
            "--baseline-manifest", f"0.15.2={baseline}",
            "--out-dir", str(out_dir),
            "--trusted-public-key", str(trust),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        digests.append(
            hashlib.sha256(
                (out_dir / "update-0.15.2-to-0.15.3-linux-standard.zip").read_bytes()
            ).hexdigest()
        )

    assert digests[0] == digests[1], "两次产出的变更包字节必须一致"


def test_delta_refuses_unverified_baseline(tmp_path: Path) -> None:
    """★ 反向断言：**换一把钥匙签的清单**不得当基线（否则中间人决定用户该下什么）。"""
    from cryptography.hazmat.primitives import serialization

    private_pem, public_pem = signing.generate_keypair()
    _other_private, other_public = signing.generate_keypair()
    key = serialization.load_pem_private_key(private_pem, password=None)
    trust = tmp_path / "trust.pem"
    trust.write_bytes(public_pem)
    elsewhere = tmp_path / "elsewhere.pem"
    elsewhere.write_bytes(other_public)

    archive = _portable_zip(tmp_path / "dist" / "pkg.zip", {"app": b"NEW"})
    baseline = _signed_manifest(tmp_path / "m.json", key, version="0.15.2", files={"app": b"OLD"})

    result = _run(
        DELTA_TOOL,
        "--payload-archive", str(archive),
        "--version", "0.15.3", "--platform", "linux", "--edition", "standard",
        "--baseline-manifest", f"0.15.2={baseline}",
        "--out-dir", str(tmp_path / "out"),
        "--trusted-public-key", str(elsewhere),
    )

    assert result.returncode != 0
    assert "校验失败" in (result.stdout + result.stderr)


def test_delta_skips_baseline_without_per_file_manifest(tmp_path: Path) -> None:
    """macOS 的 assets-only 清单没有 `payload.files` ⇒ 无法算差集 ⇒ **跳过而非硬失败**。"""
    from cryptography.hazmat.primitives import serialization

    private_pem, public_pem = signing.generate_keypair()
    key = serialization.load_pem_private_key(private_pem, password=None)
    trust = tmp_path / "trust.pem"
    trust.write_bytes(public_pem)
    archive = _portable_zip(tmp_path / "dist" / "pkg.zip", {"app": b"NEW"})
    without_payload = _signed_manifest(
        tmp_path / "macos.json", key, version="0.15.2", files={}, platform="macos", edition="standard"
    )

    result = _run(
        DELTA_TOOL,
        "--payload-archive", str(archive),
        "--version", "0.15.3", "--platform", "macos", "--edition", "standard",
        "--baseline-manifest", f"0.15.2={without_payload}",
        "--out-dir", str(tmp_path / "out"),
        "--trusted-public-key", str(trust),
    )

    assert result.returncode != 0, "一个可用基线都没有 ⇒ 明确报错"
    assert "没有任何可用的基线清单" in (result.stdout + result.stderr)


def test_manifest_tool_consumes_delta_file(tmp_path: Path) -> None:
    """④ 串起来：变更包 → `--delta-file` → 清单里出现 `payload.delta[旧版本]`。"""
    from cryptography.hazmat.primitives import serialization

    private_pem, public_pem = signing.generate_keypair()
    trust = tmp_path / "trust.pem"
    trust.write_bytes(public_pem)
    archive = _portable_zip(tmp_path / "dist" / "pkg.zip", {"app": b"NEW"})
    package = tmp_path / "update-0.15.2-to-0.15.3-linux-standard.zip"
    with zipfile.ZipFile(package, "w") as handle:
        handle.writestr("app", b"NEW")
    delta_file = tmp_path / "delta-args.txt"
    delta_file.write_text(f"0.15.2={package}\n# 注释行应被忽略\n", encoding="utf-8")

    out = tmp_path / "update-linux-standard.unsigned.json"
    result = _run(
        BUILD_TOOL,
        "--payload-archive", str(archive),
        "--version", "0.15.3", "--platform", "linux", "--edition", "standard",
        "--asset", f"linux-standard={archive}",
        "--delta-file", str(delta_file),
        "--emit-unsigned", str(out),
    )
    assert result.returncode == 0, result.stdout + result.stderr

    document = json.loads(out.read_text(encoding="utf-8"))
    entry = document["payload"]["delta"]["0.15.2"]
    assert entry["name"] == package.name
    assert entry["size"] == package.stat().st_size
    assert entry["sha256"] == hashlib.sha256(package.read_bytes()).hexdigest()

    # 签一份、再验一遍：确认这条链产出的文档仍然可被客户端接受
    key = serialization.load_pem_private_key(private_pem, password=None)
    signed = {**document, "signature": __import__("base64").b64encode(
        key.sign(canonical_feed_bytes(document))).decode()}
    feed = verify_feed_document(
        json.dumps(signed, ensure_ascii=False).encode("utf-8"),
        trusted_public_key=decode_public_key(public_pem.decode()),
    )
    assert feed.payload_delta["0.15.2"].name == package.name
