import json
import sys
from types import SimpleNamespace

import pytest

from omnicrawler.core.config import load_config
from omnicrawler.sources.frameworks import run_scrapy


def config(tmp_path):
    (tmp_path / "spider.py").write_text("# trusted local code", encoding="utf-8")
    path = tmp_path / "task.yaml"
    path.write_text("project: {name: external, workspace: work}\nsource: {kind: scrapy, spider_file: spider.py, execution_contract: trusted_external}\n", encoding="utf-8")
    return load_config(path)


def test_scrapy_zero_exit_without_output_is_not_success_and_keeps_previous(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    output = cfg.workspace / "output"
    output.mkdir(parents=True)
    previous = output / "scrapy_records.jsonl"
    previous.write_text('{"old": true}\n', encoding="utf-8")
    monkeypatch.setattr("omnicrawler.sources.frameworks._run_owned", lambda *args, **kwargs: {"returncode": 0}, raising=False)
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""))
    with pytest.raises(RuntimeError):
        run_scrapy(cfg)
    assert previous.read_text(encoding="utf-8") == '{"old": true}\n'
    assert json.loads((output / "scrapy_summary.json").read_text(encoding="utf-8"))["status"] == "failed"


def test_external_contract_requires_explicit_declaration(tmp_path):
    cfg = config(tmp_path)
    cfg.raw["source"].pop("execution_contract")
    with pytest.raises(ValueError, match="trusted_external"):
        run_scrapy(cfg)


def test_owned_process_timeout_is_reaped(tmp_path):
    from omnicrawler.sources.frameworks import _run_owned
    marker = tmp_path / "late.txt"
    script = tmp_path / "slow.py"
    script.write_text("import time\nfrom pathlib import Path\ntime.sleep(2)\nPath('late.txt').write_text('orphan')\n", encoding="utf-8")
    with pytest.raises(TimeoutError):
        _run_owned([sys.executable, str(script)], cwd=tmp_path, output=marker, logs=tmp_path,
                   timeout=0.1, maximum_bytes=1000)
    assert not marker.exists()
