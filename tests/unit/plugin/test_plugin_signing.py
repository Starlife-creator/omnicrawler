import logging
import tempfile
import unittest
from pathlib import Path

from omnicrawl.core.config import load_config
from omnicrawl.plugins import signing
from omnicrawl.plugins.ecosystem_registry import (
    EcosystemPackage,
    EcosystemRegistry,
    _ecosystem_payload,
)
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


class EcosystemSigningTest(unittest.TestCase):
    def test_verify_package_marks_signature_valid(self):
        private_pem, public_pem = signing.generate_keypair()
        package = EcosystemPackage(
            package_id="publisher.demo",
            version="1.0.0",
            publisher="Publisher",
            permissions=("records:read",),
            dependencies=(),
            license="MIT",
            compatible_core=">=1.9,<2.1",
            signature_valid=False,
            automated_tests_passed=True,
        )
        payload = _ecosystem_payload(package)
        signed = EcosystemPackage(
            package_id=package.package_id,
            version=package.version,
            publisher=package.publisher,
            permissions=package.permissions,
            dependencies=package.dependencies,
            license=package.license,
            compatible_core=package.compatible_core,
            signature_valid=False,
            automated_tests_passed=package.automated_tests_passed,
            signature=signing.sign_bytes(payload, private_pem),
            signature_algorithm="ed25519",
        )
        ok, reason = EcosystemRegistry().verify_package(signed, public_pem.decode("utf-8"))
        self.assertTrue(ok, reason)

        bad = EcosystemPackage(
            package_id=signed.package_id,
            version=signed.version,
            publisher=signed.publisher,
            permissions=signed.permissions,
            dependencies=signed.dependencies,
            license=signed.license,
            compatible_core=signed.compatible_core,
            signature_valid=False,
            automated_tests_passed=signed.automated_tests_passed,
            signature=b"not-a-real-signature",
            signature_algorithm="ed25519",
        )
        self.assertFalse(EcosystemRegistry().verify_package(bad, public_pem.decode("utf-8"))[0])


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
                load_local_plugins(registry, [str(plugin)], root, config=config)
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
                load_local_plugins(registry, [str(plugin)], root, config=config)
            self.assertTrue(any("信任根" in message for message in log.output))
            self.assertEqual(len(registry.plugins), 1)


if __name__ == "__main__":
    unittest.main()
