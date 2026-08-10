"""S2.5.41：插件模块加载缓存 + processor options 隔离 + 实例锁。"""

from __future__ import annotations

import threading
from pathlib import Path

from omnicrawl.core.config import load_config
from omnicrawl.pipeline import Pipeline
from omnicrawl.plugins import plugins as plugins_module


def test_plugin_module_cache_avoids_recompile(tmp_path: Path, monkeypatch) -> None:
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    plugin = plugin_dir / "site.py"
    plugin.write_text(
        "def register(registry):\n"
        "    registry.register_processor('cache_test', lambda config, options: object())\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        f"project: {{name: pc, workspace: '{tmp_path / 'work'}'}}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n"
        f"plugins: {{paths: [plugins/site.py], fail_open: false, signature_policy: developer}}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(plugins_module, "_PLUGIN_MODULE_CACHE", {})
    # 加载器从 exec_module 改为 compile+exec（验签字节绑定，S49）。
    # 缓存语义不变：插件模块只应被编译执行一次，第二次 Pipeline 命中缓存。
    # 注意 ast.parse 内部也会调 compile（带 PyCF_ONLY_AST），需排除。
    compiled: list[str] = []
    real_compile = compile

    def _tracked(source, filename, mode, flags=0, *args, **kwargs):
        if (
            isinstance(filename, str)
            and filename.endswith("site.py")
            and not (flags & __import__("ast").PyCF_ONLY_AST)
        ):
            compiled.append(filename)
        return real_compile(source, filename, mode, flags, *args, **kwargs)

    monkeypatch.setattr("builtins.compile", _tracked)
    with Pipeline(load_config(config_path)):
        pass
    with Pipeline(load_config(config_path)):
        pass
    assert len(compiled) == 1  # 第二次 Pipeline 复用缓存，不再编译执行


class _FakeFactory:
    def __init__(self, config, options):
        self.options = options


def test_processor_options_isolated_by_name(tmp_path: Path) -> None:
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        "project: {name: po, workspace: work}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n"
        "extract:\n"
        "  mode: html\n"
        "  processor_options:\n"
        "    custom_p:\n"
        "      custom_flag: true\n",
        encoding="utf-8",
    )
    with Pipeline(load_config(config_path)) as pipeline:
        pipeline.registry.processors["custom_p"] = _FakeFactory
        pipeline.registry.processors["custom_q"] = _FakeFactory
        p = pipeline._processor("custom_p")
        q = pipeline._processor("custom_q")
        assert isinstance(p, _FakeFactory) and isinstance(q, _FakeFactory)
        # custom_p 使用按名 options；custom_q 无按名配置 → 兜底通用 options（空）
        assert p.options.get("custom_flag") is True
        assert q.options == {}


def test_processor_instance_creation_is_thread_safe(tmp_path: Path) -> None:
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        "project: {name: pt, workspace: work}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n",
        encoding="utf-8",
    )
    with Pipeline(load_config(config_path)) as pipeline:
        results: list[object] = []
        errors: list[Exception] = []

        def _work():
            try:
                results.append(pipeline._processor("html"))
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=_work) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert not errors
        assert all(item is results[0] for item in results)
