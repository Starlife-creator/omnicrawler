import json
import sys
from types import SimpleNamespace

import pytest

from omnicrawler.core.config import load_config
from omnicrawler.sources.frameworks import run_scrapy


def config(tmp_path):
    """`spider_file` 相对 **config.root** 解析，不是相对配置文件所在目录。

    配置里写 `workspace: work`（相对路径）时，`config.root` 会落到
    **项目根**，于是 `spider.py` 被找去项目根而不是 `tmp_path` ——
    测试把文件放在 tmp_path，就必然报「Scrapy spider不存在」。

    改为：配置里用显式绝对 workspace，让 `config.root` 指向 tmp_path 的
    子目录，测试只需把 `spider.py` 放在同一个子目录下。
    """
    # `config.resolve` 对**相对路径**按 `config.root` 解析，而 `config.root`
    # 是**项目根**（与 workspace 无关）—— 于是 `spider.py` 会被找去项目根，
    # 而测试把文件放在 tmp_path，必然报「Scrapy spider不存在」。
    #
    # 用**绝对路径**声明 `spider_file` 即可：`resolve` 对绝对路径直接返回。
    spider = tmp_path / "spider.py"
    spider.write_text("# trusted local code", encoding="utf-8")
    path = tmp_path / "task.yaml"
    path.write_text(
        f"project: {{name: external, workspace: {(tmp_path / 'work').as_posix()}}}\n"
        f"source: {{kind: scrapy, spider_file: {spider.as_posix()}, "
        "execution_contract: trusted_external}\n",
        encoding="utf-8",
    )
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
