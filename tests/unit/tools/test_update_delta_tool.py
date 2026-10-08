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
import os
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
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
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


# ── 构建脚本实际调用的那条接口（此前**没有任何用例覆盖**）───────────────────
# 三个构建脚本用的是 `--baselines` + `--baseline-url-base` + `--emit-args`
# + `--emit-previous-args`，再喂给清单工具的 `--delta-file` 与 `--previous-manifest-file`。
# 这条链直到第一次真发布才会被跑到 ⇒ 参数名/文件格式一旦不一致，代价是一轮发布事故。
# 本组用例用**真的 HTTP 服务**（127.0.0.1 上临时端口）覆盖这条链，包括"某个版本没有清单"的跳过路径。


@pytest.fixture()
def baseline_server(tmp_path: Path):
    """起一个只读本地 HTTP 服务，按 `<base>/v<版本>/<文件名>` 提供清单。"""
    import http.server
    import threading

    root = tmp_path / "served"
    root.mkdir(parents=True, exist_ok=True)

    class _Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root), **kwargs)

        def log_message(self, *args):  # 静默：测试输出不需要访问日志
            return None

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield root, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_baselines_from_url_with_skip_and_both_arg_files(tmp_path: Path, baseline_server) -> None:
    """★ 构建脚本的真实调用形态：给版本号列表 ⇒ 自己取清单、跳过缺失、产出两个参数文件。"""
    from cryptography.hazmat.primitives import serialization

    served, url_base = baseline_server
    private_pem, public_pem = signing.generate_keypair()
    key = serialization.load_pem_private_key(private_pem, password=None)
    trust = tmp_path / "trust.pem"
    trust.write_bytes(public_pem)

    # 0.15.2 有清单；0.15.1 **故意没有**（模拟"那次没发或没签名"）⇒ 必须跳过而不是失败
    previous = _signed_manifest(
        served / "v0.15.2" / "update-linux-standard.json", key, version="0.15.2",
        files={"app": b"OLD", "gone.txt": b"OLD-GONE"},
    )
    assert previous.is_file()

    archive = _portable_zip(tmp_path / "dist" / "pkg.zip", {"app": b"NEW", "added": b"A"})
    delta_args = tmp_path / "scratch" / ".delta-args.txt"
    previous_args = tmp_path / "scratch" / ".previous-args.txt"

    result = _run(
        DELTA_TOOL,
        "--payload-archive", str(archive),
        "--version", "0.15.3", "--platform", "linux", "--edition", "standard",
        "--baselines", "0.15.2,0.15.1",
        "--baseline-url-base", url_base,
        "--out-dir", str(tmp_path / "delta"),
        "--scratch-dir", str(tmp_path / "scratch"),
        "--emit-args", str(delta_args),
        "--emit-previous-args", str(previous_args),
        "--trusted-public-key", str(trust),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0.15.1" in result.stderr and "跳过" in result.stderr, "缺失的基线要**可见地**跳过"

    # ① 变更包参数文件：只有取到清单的那个版本
    lines = [line for line in delta_args.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 1 and lines[0].startswith("0.15.2="), lines
    assert lines[0].endswith("update-0.15.2-to-0.15.3-linux-standard.zip")

    # ② 基线参数文件：指向**已落盘的那份**（避免清单工具再取一次网络、还可能取到不同内容）
    previous_lines = [
        line for line in previous_args.read_text(encoding="utf-8").splitlines() if line
    ]
    assert len(previous_lines) == 1 and previous_lines[0].startswith("0.15.2="), previous_lines
    saved = Path(previous_lines[0].partition("=")[2])
    assert saved.is_file() and saved.name == ".baseline-0.15.2.json"

    # ③ 把两个文件原样喂给清单工具（这才是构建脚本的实际顺序）
    out = tmp_path / "update-linux-standard.unsigned.json"
    built = _run(
        BUILD_TOOL,
        "--payload-archive", str(archive),
        "--version", "0.15.3", "--platform", "linux", "--edition", "standard",
        "--asset", f"linux-standard={archive}",
        "--delta-file", str(delta_args),
        "--previous-manifest-file", str(previous_args),
        "--trusted-public-key", str(trust),
        "--emit-unsigned", str(out),
    )
    assert built.returncode == 0, built.stdout + built.stderr

    document = json.loads(out.read_text(encoding="utf-8"))
    assert list(document["payload"]["delta"]) == ["0.15.2"], "变更包只该声明取到基线的那一版"
    # ④ 累积删除：上一版有、本版没有的路径（`gone.txt`）必须出现在删除清单里
    assert document["payload"]["deleted"] == ["gone.txt"], document["payload"]["deleted"]


def test_previous_manifest_file_accumulates_deletions(tmp_path: Path) -> None:
    """★ 累积语义：只看最近一版会让"更早删掉的残留"永远清不掉。

    `payload.deleted` 是一个**扁平清单、无条件应用**。若只算 `最近一版 − 本版`，
    那么从更老版本跳上来的用户手里那份**上一版就已删掉**的文件，既不在本版清单里、
    也不在最近一版的删除集里 ⇒ 永远留着。
    """
    from cryptography.hazmat.primitives import serialization

    private_pem, public_pem = signing.generate_keypair()
    key = serialization.load_pem_private_key(private_pem, password=None)
    trust = tmp_path / "trust.pem"
    trust.write_bytes(public_pem)

    near = _signed_manifest(
        tmp_path / "near.json", key, version="0.15.2",
        files={"keep.txt": b"K"},
        # 更早那一版删掉的，由这份清单自己记着
    )
    far = _signed_manifest(
        tmp_path / "far.json", key, version="0.15.1",
        files={"keep.txt": b"K", "deleted_long_ago.txt": b"X"},
    )
    previous_args = tmp_path / "prev.txt"
    previous_args.write_text(
        f"0.15.2={near}\n0.15.1={far}\n", encoding="utf-8"
    )
    archive = _portable_zip(tmp_path / "dist" / "pkg.zip", {"keep.txt": b"K"})
    out = tmp_path / "u.json"

    built = _run(
        BUILD_TOOL,
        "--payload-archive", str(archive),
        "--version", "0.15.3", "--platform", "linux", "--edition", "standard",
        "--asset", f"linux-standard={archive}",
        "--previous-manifest-file", str(previous_args),
        "--trusted-public-key", str(trust),
        "--emit-unsigned", str(out),
    )
    assert built.returncode == 0, built.stdout + built.stderr

    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["payload"]["deleted"] == ["deleted_long_ago.txt"], document["payload"]["deleted"]


def test_previous_manifest_file_requires_a_verified_baseline(tmp_path: Path) -> None:
    """★ 反向断言：基线清单**必须验签** ⇒ 换一把钥匙签的清单不得当基线。"""
    from cryptography.hazmat.primitives import serialization

    private_pem, public_pem = signing.generate_keypair()
    _other_private, other_public = signing.generate_keypair()
    key = serialization.load_pem_private_key(private_pem, password=None)
    trust = tmp_path / "trust.pem"
    trust.write_bytes(public_pem)
    elsewhere = tmp_path / "elsewhere.pem"
    elsewhere.write_bytes(other_public)

    baseline = _signed_manifest(tmp_path / "b.json", key, version="0.15.2", files={"a": b"1"})
    previous_args = tmp_path / "prev.txt"
    previous_args.write_text(f"0.15.2={baseline}\n", encoding="utf-8")
    archive = _portable_zip(tmp_path / "dist" / "pkg.zip", {"a": b"2"})

    built = _run(
        BUILD_TOOL,
        "--payload-archive", str(archive),
        "--version", "0.15.3", "--platform", "linux", "--edition", "standard",
        "--asset", f"linux-standard={archive}",
        "--previous-manifest-file", str(previous_args),
        "--trusted-public-key", str(elsewhere),
        "--emit-unsigned", str(tmp_path / "u.json"),
    )

    assert built.returncode != 0
    assert "校验失败" in (built.stdout + built.stderr)


def test_empty_arg_files_produce_a_manifest_without_delta(tmp_path: Path) -> None:
    """★ **0.15.0 首发的真实形态**：基线全取不到 ⇒ 两个参数文件为空 ⇒
    清单必须**照常产出**（只是没有 `payload.delta` / `payload.deleted`），而不是失败。

    为什么必须钉这条：构建脚本是**先创建空文件**（`: > "$DELTA_ARGS_FILE"`）再调用清单工具的；
    首个版本一定走到这里（v0.14.0 及更早都没有任何 `update-*.json`）。空文件若被判成
    "格式错误"，第一次发布就红。
    """
    delta_args = tmp_path / ".delta-args.txt"
    previous_args = tmp_path / ".previous-args.txt"
    delta_args.write_text("", encoding="utf-8")
    previous_args.write_text("", encoding="utf-8")
    archive = _portable_zip(tmp_path / "dist" / "pkg.zip", {"app": b"NEW"})
    out = tmp_path / "u.json"

    built = _run(
        BUILD_TOOL,
        "--payload-archive", str(archive),
        "--version", "0.15.0", "--platform", "linux", "--edition", "standard",
        "--asset", f"linux-standard={archive}",
        "--delta-file", str(delta_args),
        "--previous-manifest-file", str(previous_args),
        "--emit-unsigned", str(out),
    )

    assert built.returncode == 0, built.stdout + built.stderr
    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["payload"]["delta"] == {}
    assert document["payload"]["deleted"] == []
    assert set(document["payload"]["files"]) == {"app"}
    # 清单本体仍然可验签（首发客户端的唯一依赖）
    assert "signature" not in document  # 无签名清单，签名由维护者本机做
    assert document["platform"] == "linux" and document["edition"] == "standard"
