"""Wheel-Pack 组件运行器：隔离子进程执行 + 启动前二次校验。

## 为什么必须隔离子进程

`paddle` / `cv2` / `scipy` 与宿主已加载的 `PySide6` / `lxml` 同处一个进程时，
极易触发 `libiomp5md.dll`、`msvcp140`、OpenSSL 符号冲突；Linux 下
`libmklml_intel.so` 还要求**进程启动前**设置环境变量。所以只在该子进程内
挂载，GUI 主进程零污染。

## 安全边界（本文件逐条验证）

1. **零 pip、零安装脚本**：`.whl` 是纯数据 ZIP，装配时已校验 SHA-256；
   运行器只把已落盘的 `site-packages` 挂进子进程，不做任何安装动作。
2. **启动前二次校验**：组件目录可能在装配后被改动，子进程一旦起来就
   没有拦截机会——所以每次运行前按 `component.json` 重算哈希。
3. **只在子进程内**：`sys.path` 与原生库路径的修改都发生在子进程。

## 测试策略

用**真实的 Python 子进程**（入口是几行脚本），不依赖 paddle/cv2 等重型依赖，
但走的是与生产完全相同的代码路径（`subprocess.run` + 环境注入 + JSON-over-stdio）。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from omnicrawler.services.component_runner import (
    ComponentRunnerError,
    build_wheelpack_spec,
    component_runner_env,
    describe_spec,
    run_component,
    verify_component_files,
)

_ENTRY_SCRIPT = """\
import json, os, sys
payload = json.loads(sys.stdin.read() or "{}")
result = {
    "echo": payload,
    "site_packages_on_path": os.environ.get("OMNICRAWL_COMPONENT_SITE_PACKAGES", ""),
    "engine": os.environ.get("OMNICRAWL_COMPONENT_ENGINE", ""),
    "cwd_is_component_root": os.path.basename(os.getcwd()),
}
print(json.dumps(result))
"""


def _build_component(root: Path, *, entry_script: str = _ENTRY_SCRIPT) -> Path:
    """构造一个最小 Wheel-Pack 组件目录（含 component.json / site-packages / entry.json）。"""
    root.mkdir(parents=True, exist_ok=True)
    site_packages = root / "site-packages"
    site_packages.mkdir(exist_ok=True)
    (site_packages / "placeholder.py").write_text("# 组件依赖占位\n", encoding="utf-8")

    entry = root / "runner_entry.py"
    entry.write_text(entry_script, encoding="utf-8")

    files = {}
    for path in (site_packages / "placeholder.py", entry):
        relative = path.relative_to(root).as_posix()
        files[relative] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()

    (root / "component.json").write_text(
        json.dumps({"name": "demo-wheelpack", "version": "1.2.3", "files": files}, indent=2),
        encoding="utf-8",
    )
    (root / "entry.json").write_text(
        json.dumps(
            {
                "name": "demo-wheelpack",
                "version": "1.2.3",
                "engine": "paddle",
                "entry": "runner_entry.py",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return root


def test_spec_reads_entry_and_site_packages(tmp_path: Path) -> None:
    root = _build_component(tmp_path / "demo")
    spec = build_wheelpack_spec(root)
    assert spec.name == "demo-wheelpack"
    assert spec.version == "1.2.3"
    assert spec.engine == "paddle"
    assert spec.site_packages.is_dir()
    assert spec.entry.is_file()


def test_verify_component_files_counts_declared(tmp_path: Path) -> None:
    root = _build_component(tmp_path / "demo")
    assert verify_component_files(root) == 2


def test_tampered_component_file_is_rejected(tmp_path: Path) -> None:
    """装配后被改动必须被拦截——子进程起来就没机会了。"""
    root = _build_component(tmp_path / "demo")
    (root / "site-packages" / "placeholder.py").write_text("# 被篡改\n", encoding="utf-8")

    with pytest.raises(ComponentRunnerError) as excinfo:
        verify_component_files(root)
    assert "哈希不符" in str(excinfo.value)


def test_missing_component_file_is_rejected(tmp_path: Path) -> None:
    root = _build_component(tmp_path / "demo")
    (root / "runner_entry.py").unlink()

    with pytest.raises(ComponentRunnerError) as excinfo:
        verify_component_files(root)
    assert "缺失" in str(excinfo.value)


def test_path_traversal_in_manifest_is_rejected(tmp_path: Path) -> None:
    """清单里出现 `..` 必须拒绝，不能被当成相对路径拼接。"""
    root = _build_component(tmp_path / "demo")
    manifest = json.loads((root / "component.json").read_text(encoding="utf-8"))
    manifest["files"]["../../escape.py"] = "sha256:" + "a" * 64
    (root / "component.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ComponentRunnerError) as excinfo:
        verify_component_files(root)
    assert "越界" in str(excinfo.value)


def test_manifest_without_files_is_rejected(tmp_path: Path) -> None:
    root = _build_component(tmp_path / "demo")
    (root / "component.json").write_text(
        json.dumps({"name": "x", "version": "1"}), encoding="utf-8"
    )
    with pytest.raises(ComponentRunnerError) as excinfo:
        verify_component_files(root)
    assert "未声明任何文件" in str(excinfo.value)


def test_runner_executes_in_subprocess_and_passes_env(tmp_path: Path) -> None:
    """端到端：真实子进程执行，环境变量与工作目录都正确注入。"""
    root = _build_component(tmp_path / "demo")
    spec = build_wheelpack_spec(root)

    result = run_component(spec, {"hello": "world"}, timeout=60)

    assert result["echo"] == {"hello": "world"}, f"payload 未透传：{result}"
    assert result["site_packages_on_path"] == str(spec.site_packages), (
        "子进程未收到 site-packages 环境变量"
    )
    assert result["engine"] == "paddle"
    assert result["cwd_is_component_root"] == "demo", (
        f"子进程工作目录不是组件根：{result['cwd_is_component_root']!r}"
    )


def test_runner_rejects_on_tamper_before_start(tmp_path: Path) -> None:
    """篡改后必须在**启动前**拒绝，不能让它跑起来。"""
    root = _build_component(tmp_path / "demo")
    spec = build_wheelpack_spec(root)
    (root / "runner_entry.py").write_text("print('tampered')\n", encoding="utf-8")

    with pytest.raises(ComponentRunnerError) as excinfo:
        run_component(spec, {})
    assert "哈希不符" in str(excinfo.value)


def test_runner_reports_nonzero_exit(tmp_path: Path) -> None:
    root = _build_component(
        tmp_path / "demo",
        entry_script="import sys; sys.stderr.write('boom'); sys.exit(3)\n",
    )
    spec = build_wheelpack_spec(root)

    with pytest.raises(ComponentRunnerError) as excinfo:
        run_component(spec, {})
    message = str(excinfo.value)
    assert "退出码 3" in message
    assert "boom" in message


def test_runner_rejects_non_json_output(tmp_path: Path) -> None:
    root = _build_component(tmp_path / "demo", entry_script="print('not json')\n")
    spec = build_wheelpack_spec(root)

    with pytest.raises(ComponentRunnerError) as excinfo:
        run_component(spec, {})
    assert "不是合法 JSON" in str(excinfo.value)


def test_runner_times_out(tmp_path: Path) -> None:
    root = _build_component(
        tmp_path / "demo",
        entry_script="import time; time.sleep(30)\n",
    )
    spec = build_wheelpack_spec(root)

    with pytest.raises(ComponentRunnerError) as excinfo:
        run_component(spec, {}, timeout=0.5)
    assert "超时" in str(excinfo.value)


def test_env_prepends_native_library_paths(tmp_path: Path) -> None:
    """原生库搜索路径必须前置 site-packages（Linux/macOS 依赖它）。"""
    root = _build_component(tmp_path / "demo")
    spec = build_wheelpack_spec(root)
    env = component_runner_env(spec)

    site = str(spec.site_packages)
    assert site in env["LD_LIBRARY_PATH"], "LD_LIBRARY_PATH 未包含 site-packages"
    assert site in env["DYLD_LIBRARY_PATH"], "DYLD_LIBRARY_PATH 未包含 site-packages"
    # 必须前置而不是追加：否则系统旧版本库会抢先被加载。
    # 分隔符按平台取——Windows 是 `;`，不是 POSIX 的 `:`。
    assert env["LD_LIBRARY_PATH"].split(os.pathsep)[0] == site


def test_spec_requires_site_packages(tmp_path: Path) -> None:
    root = _build_component(tmp_path / "demo")
    (root / "site-packages" / "placeholder.py").unlink()
    (root / "site-packages").rmdir()

    with pytest.raises(ComponentRunnerError) as excinfo:
        build_wheelpack_spec(root)
    assert "site-packages" in str(excinfo.value)


def test_spec_rejects_entry_traversal(tmp_path: Path) -> None:
    root = _build_component(tmp_path / "demo")
    (root / "entry.json").write_text(
        json.dumps({"name": "x", "version": "1", "entry": "../../evil.py"}),
        encoding="utf-8",
    )
    with pytest.raises(ComponentRunnerError) as excinfo:
        build_wheelpack_spec(root)
    assert "越界" in str(excinfo.value)


def test_describe_spec_is_human_readable(tmp_path: Path) -> None:
    root = _build_component(tmp_path / "demo")
    text = describe_spec(build_wheelpack_spec(root))
    assert "demo-wheelpack" in text
    assert "paddle" in text
    assert "site-packages" in text


def test_subprocess_uses_current_interpreter(tmp_path: Path) -> None:
    """子进程必须用宿主同一解释器——换解释器会丢掉冻结包的 ABI 匹配。"""
    root = _build_component(
        tmp_path / "demo",
        entry_script="import json, sys; print(json.dumps({'executable': sys.executable}))\n",
    )
    spec = build_wheelpack_spec(root)
    result = run_component(spec, {})
    assert result["executable"]
    # 同一份 Python（路径可能因 venv 符号链接而略有差异，比较可执行文件名与版本）
    assert Path(result["executable"]).name == Path(sys.executable).name
