# ruff: noqa: E402 —— 导入顺序刻意先声明模块级 fixture，再导入被测模块。

"""基准的「可复现 + 公平对比」端到端回归。

对应 `优化方案.md` §1.2 采集能力的升级项：结果必须「可复现、可比较」。
本测试锁定三件事，任何一件回退都会让基准重新变成没有意义的一堆数字：

1. **档位真正施加**——同一份配置在不同档位下必须跑出**不同的负载**。
   2026-09-11 之前 profile 只作为标签记录，`concurrency` / `delay` / `timeout` /
   `max_pages` 从未写入运行配置，三个档位跑的是同一份负载。
2. **指标不是 0**——`ApplicationService.run` 返回展平摘要，旧代码读
   `result["stats"]["responses"]`（不存在该子键），导致 `pages_per_second`
   长期恒为 0.0：基准从未真正测到过吞吐量。
3. **不可用运行不得成为基线**——全失败的运行流水线仍会报 `succeeded` 且
   `pages=0`；若按「无异常即成功」入库，该档位的退化检测会静默失效。
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from omnicrawler.core.config import load_config
from omnicrawler.services.benchmarking import (
    BenchmarkHistory,
    BenchmarkProfile,
    BenchmarkRunner,
    apply_profile,
)

_PAGES = 12
_LINKS = "".join(f'<a href="/p{i}">{i}</a>' for i in range(1, _PAGES + 1))
_INDEX = f"<html><body><h1>index</h1>{_LINKS}</body></html>".encode()
_ITEM = b"<html><body><h1>detail</h1><p>content</p></body></html>"

_TEMPLATE = Path(__file__).resolve().parents[2] / "configs" / "full_pipeline.yaml"

#: 零延迟档位：只为验证负载确实随档位变化，不追求真实吞吐。
_SMALL = BenchmarkProfile("small", concurrency=1, delay_seconds=0.0, timeout_seconds=10, max_pages=3)
_LARGE = BenchmarkProfile("large", concurrency=4, delay_seconds=0.0, timeout_seconds=10, max_pages=_PAGES)


class _CountingSite(BaseHTTPRequestHandler):
    """记录收到的请求路径，用于直接证明档位的抓取上限生效。"""

    def do_GET(self):  # noqa: N802
        self.server.hits.append(self.path)  # type: ignore[attr-defined]
        body = _INDEX if self.path == "/" else _ITEM
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # noqa: N802
        return


@pytest.fixture
def site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CountingSite)
    server.hits = []  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def _write_config(tmp_path: Path, seed: str) -> Path:
    """从仓库模板派生一份本机可跑的配置（关掉需要随附模板的 PDF 处理器）。"""
    if not _TEMPLATE.is_file():
        pytest.skip(f"缺少仓库基准模板: {_TEMPLATE}")
    data = yaml.safe_load(_TEMPLATE.read_text(encoding="utf-8"))
    data["processors"]["pdf"]["enabled"] = False
    data["project"].update(name="bench_fairness", workspace=str(tmp_path / "work"))
    data["source"]["seeds"] = [seed]
    # 与配置自身的 max_pages 刻意设成不同值：档位若真生效，应以档位为准。
    data["crawl"].update(allow_domains=["127.0.0.1"], max_pages=500, max_depth=4)
    data["http"].update(allow_private_network=True, respect_robots=False, retries=0)
    path = tmp_path / "task.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def test_profiles_apply_distinct_workloads(site, tmp_path: Path) -> None:
    """档位必须真正改变负载：上限不同 → 抓取量不同。"""
    path = _write_config(tmp_path, f"http://127.0.0.1:{site.server_port}/")
    runner = BenchmarkRunner(profiles={"small": _SMALL, "large": _LARGE})

    site.hits.clear()
    small = runner.run("small", config_path=path, workdir=tmp_path)
    small_hits = len(site.hits)

    site.hits.clear()
    large = runner.run("large", config_path=path, workdir=tmp_path)
    large_hits = len(site.hits)

    # 配置自身写的是 500；两个档位都必须把它覆盖掉。
    assert small_hits <= _SMALL.max_pages, f"small 抓了 {small_hits} 次，超出档位上限"
    assert large_hits > small_hits, f"large({large_hits}) 未比 small({small_hits}) 抓得多——档位未生效"
    assert small_hits < 500, "档位未覆盖配置自身的 max_pages=500"

    # 指标必须来自真实数据，而不是恒为 0。
    for result, hits, profile in ((small, small_hits, _SMALL), (large, large_hits, _LARGE)):
        assert result.usable, (result.ok, result.status, result.pages)
        assert result.pages == hits, f"pages={result.pages} 与站点收到的请求数 {hits} 不一致"
        assert result.pages_per_second > 0, "吞吐量仍为 0 —— 指标口径回退了"
        assert result.bytes_transferred > 0, "落库字节数仍为 0"
        assert dict(result.profile_settings)["max_pages"] == str(profile.max_pages)


def test_result_carries_reproducibility_metadata(site, tmp_path: Path) -> None:
    """结果须自带复现信息：源/生效配置指纹、档位参数、环境。"""
    path = _write_config(tmp_path, f"http://127.0.0.1:{site.server_port}/")
    runner = BenchmarkRunner(profiles={"small": _SMALL})
    result = runner.run("small", config_path=path, workdir=tmp_path)

    assert result.config_sha256, "缺少源配置指纹"
    assert result.effective_config_sha256, "缺少生效配置指纹"
    assert result.config_sha256 != result.effective_config_sha256, "档位未改变配置内容"
    assert dict(result.profile_settings)["max_pages"] == str(_SMALL.max_pages)
    environment = dict(result.environment)
    assert environment["package_version"], "缺少包版本"
    assert environment["python"], "缺少 Python 版本"
    assert environment["platform"], "缺少平台"


def test_derived_config_is_loadable_and_matches_profile(site, tmp_path: Path) -> None:
    """派生配置必须能重新加载，且确实带着档位参数——复现的立足点。"""
    path = _write_config(tmp_path, f"http://127.0.0.1:{site.server_port}/")
    config = load_config(path)
    effective = apply_profile(config.raw, _SMALL)
    assert effective["crawl"]["max_pages"] == _SMALL.max_pages
    assert effective["http"]["delay_seconds"] == _SMALL.delay_seconds
    # 源配置不得被就地改动。
    assert config.raw["crawl"]["max_pages"] == 500
    assert config.raw["http"]["delay_seconds"] != _SMALL.delay_seconds


def test_unusable_run_never_becomes_baseline(tmp_path: Path) -> None:
    """全失败的运行不得成为基线（实测：它会以 pages=0 且 status=succeeded 入库）。"""
    dead = tmp_path / "dead.yaml"
    data = yaml.safe_load(_TEMPLATE.read_text(encoding="utf-8"))
    data["processors"]["pdf"]["enabled"] = False
    data["project"].update(name="bench_dead", workspace=str(tmp_path / "dead_work"))
    data["source"]["seeds"] = ["http://127.0.0.1:9/"]  # 本机不可达端口
    data["crawl"].update(allow_domains=["127.0.0.1"], max_pages=5, max_depth=2)
    data["http"].update(allow_private_network=True, respect_robots=False, retries=0)
    dead.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    runner = BenchmarkRunner(profiles={"small": _SMALL})
    failed = runner.run("small", config_path=dead, workdir=tmp_path)
    assert failed.pages == 0
    assert not failed.usable, "抓不到任何页面的运行不应算可用"

    history = BenchmarkHistory(tmp_path / "history.json")
    history.add(failed)
    assert history.baseline("small") is None, "不可用运行被当成了基线"
