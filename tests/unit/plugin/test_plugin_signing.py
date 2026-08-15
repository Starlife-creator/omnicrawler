import logging
import tempfile
import unittest
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from omnicrawl.core.config import load_config
from omnicrawl.plugins import signing
from omnicrawl.plugins.plugins import Registry, load_local_plugins


class SigningPrimitiveTest(unittest.TestCase):
    def test_roundtrip_generate_sign_verify(self):
        private_pem, public_pem = signing.generate_keypair()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plugin = root / "sample_plugin.py"
            plugin.write_text("def register(registry): pass\n", encoding="utf-8")
            sig = signing.sign_file(plugin, private_pem)
            self.assertTrue(sig.is_file())
            ok, reason = signing.verify_plugin(plugin, public_pem.decode("utf-8"))
            self.assertTrue(ok, reason)

    def test_tampered_file_fails(self):
        private_pem, public_pem = signing.generate_keypair()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plugin = root / "p.py"
            plugin.write_text("def register(registry): pass\n", encoding="utf-8")
            signing.sign_file(plugin, private_pem)
            plugin.write_text("def register(registry): pass\n# tampered\n", encoding="utf-8")
            ok, reason = signing.verify_plugin(plugin, public_pem.decode("utf-8"))
            self.assertFalse(ok)
            self.assertIn("篡改", reason)

    def test_missing_sig_fails(self):
        _, public_pem = signing.generate_keypair()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plugin = root / "p.py"
            plugin.write_text("def register(registry): pass\n", encoding="utf-8")
            ok, reason = signing.verify_plugin(plugin, public_pem.decode("utf-8"))
            self.assertFalse(ok)
            self.assertIn(".sig", reason)

    def test_wrong_key_rejected(self):
        private_a, _ = signing.generate_keypair()
        _, public_b = signing.generate_keypair()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plugin = root / "p.py"
            plugin.write_text("def register(registry): pass\n", encoding="utf-8")
            signing.sign_file(plugin, private_a)
            ok, _ = signing.verify_plugin(plugin, public_b.decode("utf-8"))
            self.assertFalse(ok)

    def test_verify_requires_trust_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plugin = root / "p.py"
            plugin.write_text("x = 1\n", encoding="utf-8")
            ok, reason = signing.verify_plugin(plugin, "")
            self.assertFalse(ok)
            self.assertIn("信任根", reason)


class LoaderSignatureGateTest(unittest.TestCase):
    def _config(self, temp: Path, plugins_yaml: str):
        cfg = temp / "project.yaml"
        cfg.write_text(
            "project: {name: test, workspace: work}\n"
            "source: {kind: static_html, seeds: [https://example.com/]}\n"
            f"plugins: {plugins_yaml}\n",
            encoding="utf-8",
        )
        return load_config(cfg)

    def test_unsigned_plugin_rejected_when_trust_configured(self):
        private_pem, public_pem = signing.generate_keypair()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plugin = root / "p.py"
            plugin.write_text("def register(registry): pass\n", encoding="utf-8")
            trust = root / "trust.pub.pem"
            trust.write_bytes(public_pem)
            config = self._config(root, f"{{paths: [{plugin}], trust_public_key: {str(trust)!r}}}")
            registry = Registry()
            with self.assertRaises(signing.PluginSignatureError):
                load_local_plugins(
                    registry, [str(plugin)], root, config=config,
                    signature_policy="strict",
                )
            self.assertEqual(registry.plugins, [])

    def test_signed_plugin_loads_when_trust_configured(self):
        private_pem, public_pem = signing.generate_keypair()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plugin = root / "p.py"
            plugin.write_text(
                "def register(registry): registry.register_source('x', object)\n",
                encoding="utf-8",
            )
            signing.sign_file(plugin, private_pem)
            trust = root / "trust.pub.pem"
            trust.write_bytes(public_pem)
            config = self._config(root, f"{{paths: [{plugin}], trust_public_key: {str(trust)!r}}}")
            registry = Registry()
            load_local_plugins(registry, [str(plugin)], root, config=config)
            self.assertEqual(len(registry.plugins), 1)

    def test_no_trust_root_warns_and_loads(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plugin = root / "p.py"
            plugin.write_text("def register(registry): pass\n", encoding="utf-8")
            config = self._config(root, f"{{paths: [{plugin}]}}")
            registry = Registry()
            with self.assertLogs(level=logging.WARNING) as log:
                load_local_plugins(
                    registry, [str(plugin)], root, config=config,
                    signature_policy="developer",  # 显式开发模式：无信任根时放行+告警
                )
            self.assertTrue(any("信任根" in message for message in log.output))
            self.assertEqual(len(registry.plugins), 1)


if __name__ == "__main__":
    unittest.main()
