from tools.generate_checksums import _sha256, main, verify_manifest


def test_manifest_verification_accepts_valid_release_file(tmp_path):
    artifact = tmp_path / "artifact.zip"
    artifact.write_bytes(b"release")
    manifest = tmp_path / "SHA256SUMS.txt"
    manifest.write_text(f"{_sha256(artifact)}  {artifact.name}\n", encoding="ascii")

    assert verify_manifest(tmp_path, manifest) == []


def test_manifest_verification_rejects_tampering_and_traversal(tmp_path):
    artifact = tmp_path / "artifact.zip"
    artifact.write_bytes(b"tampered")
    manifest = tmp_path / "SHA256SUMS.txt"
    manifest.write_text("0" * 64 + "  artifact.zip\n" + "1" * 64 + "  ../escape.zip\n", encoding="ascii")

    issues = verify_manifest(tmp_path, manifest)
    assert any("不匹配" in issue for issue in issues)
    assert any("不安全路径" in issue for issue in issues)


def test_cli_generates_utf8_manifest_that_can_be_verified(tmp_path, monkeypatch):
    (tmp_path / "artifact.zip").write_bytes(b"release")
    monkeypatch.setattr("sys.argv", ["generate_checksums", str(tmp_path), "--output", "SHA256SUMS.txt"])
    assert main() == 0
    manifest = tmp_path / "SHA256SUMS.txt"
    assert "校验清单" in manifest.read_text(encoding="utf-8")
    assert verify_manifest(tmp_path, manifest) == []
