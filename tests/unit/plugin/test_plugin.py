import tempfile
import unittest
from pathlib import Path

from omnicrawler.core.config import load_config
from omnicrawler.pipeline import build_registry
from omnicrawler.plugins.plugins import Registry


class PluginTest(unittest.TestCase):
    def test_permission_is_denied_before_plugin_module_executes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            marker = root / "executed.txt"
            plugin = root / "dangerous.py"
            plugin.write_text(
                "PLUGIN_METADATA = {'name': 'dangerous', 'permissions': ['network']}\n"
                f"open({str(marker)!r}, 'w').write('executed')\n"
                "def register(registry): pass\n",
                encoding="utf-8",
            )
            config_path = root / "project.yaml"
            config_path.write_text(
                f"project: {{name: test, workspace: work}}\n"
                f"source: {{kind: static_html, seeds: [https://example.com/]}}\n"
                f"plugins: {{paths: [{plugin}]}}\n",
                encoding="utf-8",
            )

            with self.assertRaises(PermissionError):
                build_registry(load_config(config_path))
            self.assertFalse(marker.exists())

    def test_extended_plugin_contract_and_hooks(self):
        registry = Registry()
        calls = []
        registry.register_auth_provider("cookie", lambda: object())
        registry.register_parser("document", lambda: object())
        registry.register_extractor("records", lambda: object())
        registry.register_transformer("normalize", lambda: object())
        registry.register_hook("after_extract", lambda **context: calls.append(context["count"]))

        registry.emit("after_extract", count=3)
        description = registry.describe()

        self.assertEqual(calls, [3])
        self.assertIn("cookie", description["auth_providers"])
        self.assertIn("document", description["parsers"])
        self.assertIn("records", description["extractors"])
        self.assertEqual(description["hooks"]["after_extract"], 1)

    def test_optional_plugin_can_fail_open(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = root / "project.yaml"
            config_path.write_text(
                "project: {name: test, workspace: work}\n"
                "source: {kind: static_html, seeds: [https://example.com/]}\n"
                "plugins: {paths: [missing.py], fail_open: true}\n",
                encoding="utf-8",
            )
            registry = build_registry(load_config(config_path))

            self.assertEqual(len(registry.plugin_errors), 1)
            self.assertIn("missing.py", registry.plugin_errors[0]["path"])

    def test_local_source_plugin(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plugin = root / "plugin.py"
            plugin.write_text(
                """
from omnicrawler.sources.sources import GenericSource
class CustomSource(GenericSource):
    pass
def register(registry):
    registry.register_source('custom_test', CustomSource)
""",
                encoding="utf-8",
            )
            config_path = root / "project.yaml"
            config_path.write_text(
                f"project: {{name: test, workspace: work}}\nsource: {{kind: custom_test, seeds: [https://example.com/]}}\nplugins: {{paths: [{plugin}], signature_policy: developer}}\n",
                encoding="utf-8",
            )
            config = load_config(config_path)
            registry = build_registry(config)
            self.assertIn("custom_test", registry.sources)


if __name__ == "__main__":
    unittest.main()
