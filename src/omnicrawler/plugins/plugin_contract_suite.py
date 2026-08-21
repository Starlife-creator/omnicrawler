"""F1 公共契约测试夹具（Phase 2a）：契约 2 插件的统一验收套件。

作者本地 ``pytest -m plugin_contract`` 与 CI 跑**同一批测试**（"本地绿 = CI
绿"，F1）——夹具随核心版本发布，降低审核摩擦与误拒。

用法（作者，在自己的插件测试文件内继承套件类并覆盖 ``contract_plugin_dir``）::

    import pytest
    from pathlib import Path
    from omnicrawler.plugins.plugin_contract_suite import Contract2Suite

    class TestMyPlugin(Contract2Suite):
        @pytest.fixture(scope="class")
        def contract_plugin_dir(self):
            return Path(__file__).parent

套件按插件**静态声明**自适应：未声明的能力对应能力用例自动跳过（只验证
插件声明过的面），隔离与协议用例对所有契约 2 插件强制生效。

实现说明：采用 xunit 类继承而非跨模块 import——pytest 对导入的 test 函数
按 ``__module__`` 去重收集、夹具按定义模块解析，跨模块共享套件不可靠；
类继承天然被收集且夹具沿 MRO 解析，是共享测试套件的稳健形态。
"""

from __future__ import annotations

import ast
import shutil
import tempfile
from pathlib import Path

import pytest

from .plugin_router import detect_contract_shape
from .plugin_sandbox import PluginSubprocessSession

pytestmark = pytest.mark.plugin_contract


class Contract2Suite:
    """契约 2 插件公共验收套件（继承即生效）。"""

    # ---- 作者覆盖点 ----

    @pytest.fixture(scope="class")
    @staticmethod
    def contract_plugin_dir() -> Path:
        pytest.skip("作者必须覆盖 contract_plugin_dir fixture 指向插件目录")

    # ---- 派生夹具 ----

    @pytest.fixture(scope="class")
    @staticmethod
    def contract_entry_module(contract_plugin_dir: Path) -> str:
        if (contract_plugin_dir / "plugin.py").is_file():
            return "plugin"
        candidates = [
            p.stem
            for p in contract_plugin_dir.glob("*.py")
            if detect_contract_shape(p.read_text(encoding="utf-8")) == 2
        ]
        assert len(candidates) == 1, f"无法确定唯一契约 2 入口模块: {candidates}"
        return candidates[0]

    @pytest.fixture(scope="class")
    @staticmethod
    def contract_metadata(contract_plugin_dir: Path) -> dict:
        for py in contract_plugin_dir.glob("*.py"):
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == "PLUGIN_METADATA":
                            value = ast.literal_eval(node.value)
                            if isinstance(value, dict):
                                return value
        return {}

    # ---- 强制面：契约形态 / 隔离 / 协议 ----

    def test_contract2_shape(self, contract_plugin_dir, contract_entry_module):
        source = (contract_plugin_dir / f"{contract_entry_module}.py").read_text(encoding="utf-8")
        assert detect_contract_shape(source) == 2, "插件必须导出顶层 handle（契约 2）"

    def test_sandbox_blocks_host_import(self):
        """沙箱隔离生效性：子进程内 import omnicrawler 必失败。"""
        probe_src = (
            "def handle(op, p):\n"
            "    try:\n"
            "        import omnicrawler\n"
            "        return {'imported': True}\n"
            "    except ImportError:\n"
            "        return {'imported': False}\n"
        )
        probe_dir = Path(tempfile.mkdtemp(prefix="contract-probe-"))
        try:
            (probe_dir / "probe.py").write_text(probe_src, encoding="utf-8")
            with PluginSubprocessSession(probe_dir, "probe", timeout_seconds=30) as session:
                assert session.call("x", {})["imported"] is False
        finally:
            shutil.rmtree(probe_dir, ignore_errors=True)

    def test_handle_returns_dict(self, contract_plugin_dir, contract_entry_module):
        """任意合法操作的返回值必须是 dict（协议不变式）。"""
        with PluginSubprocessSession(
            contract_plugin_dir, contract_entry_module, timeout_seconds=30
        ) as session:
            result = session.call("contract.probe", {})
            assert isinstance(result, dict)

    def test_session_end_lifecycle(self, contract_plugin_dir, contract_entry_module):
        """session 模式：多次调用复用同一进程，end 后进程回收。"""
        session = PluginSubprocessSession(
            contract_plugin_dir, contract_entry_module, timeout_seconds=30
        )
        session.start()
        first_proc = session._proc
        session.call("contract.probe", {})
        session.call("contract.probe", {})
        assert session._proc is first_proc, "会话内调用必须复用同一进程"
        session.end()
        assert session._proc is None

    # ---- 自适应面：按静态声明的 permissions 启用 ----

    def test_permission_denied_for_undeclared_capability(self, contract_metadata):
        """未声明 records:read 时，records.read 必被 E_PERMISSION 拒绝。"""
        declared = {str(p).casefold() for p in contract_metadata.get("permissions", [])}
        if "records:read" in declared:
            pytest.skip("插件声明了 records:read，无越界面可验")
        probe_src = (
            "import omnicrawler_sdk\n"
            "def handle(op, p):\n"
            "    try:\n"
            "        omnicrawler_sdk.call('records.read', {})\n"
            "        return {'allowed': True}\n"
            "    except RuntimeError as e:\n"
            "        return {'allowed': False, 'code': str(e).split(':')[0]}\n"
        )
        probe_dir = Path(tempfile.mkdtemp(prefix="contract-perm-"))
        try:
            (probe_dir / "probe.py").write_text(probe_src, encoding="utf-8")
            # 须经 broker + drive_loop 应答 capability 请求（裸 session.call
            # 不处理 capability 行，探针会收不到 E_PERMISSION 应答）
            from .plugin_broker import CapabilityBroker, drive_loop

            session = PluginSubprocessSession(probe_dir, "probe", timeout_seconds=30)
            session.start()
            broker = CapabilityBroker(permissions=set(), system_info={"version": "suite"})
            result = drive_loop(session, broker, "x", {}, timeout_seconds=0)
            session.end()
            assert result["allowed"] is False
            assert result["code"] == "E_PERMISSION"
        finally:
            shutil.rmtree(probe_dir, ignore_errors=True)

    def test_metadata_is_static_literal(self, contract_plugin_dir):
        """PLUGIN_METADATA 必须为静态字面量（运行期权限 ⊆ 静态审批的前提）。"""
        found = False
        for py in contract_plugin_dir.glob("*.py"):
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == "PLUGIN_METADATA":
                            found = True
                            value = ast.literal_eval(node.value)  # 非字面量即抛异常
                            assert isinstance(value, dict)
        assert found, "契约 2 插件必须声明 PLUGIN_METADATA 静态字面量"

    def test_manifest_fields(self, contract_metadata):
        """清单最小字段集：name/version 必填，execution_mode 合法枚举。"""
        assert contract_metadata.get("name"), "PLUGIN_METADATA.name 必填"
        assert contract_metadata.get("version"), "PLUGIN_METADATA.version 必填"
        mode = str(contract_metadata.get("execution_mode", "subprocess")).strip()
        assert mode in ("subprocess", "in_process"), f"execution_mode 非法: {mode!r}"
