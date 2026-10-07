import copy
import gzip
import json

import pytest

from omnicrawler.core.config import DEFAULTS, AppConfig
from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.extraction.extractors import HTMLProcessor
from omnicrawler.services.regression_library import RegressionLibrary, verify_regression_fixtures


def setup_fixture(tmp_path):
    raw = copy.deepcopy(DEFAULTS)
    raw["extract"] = {"mode": "html", "fields": {"title": {"selector": "h1"}}}
    config = AppConfig(tmp_path / "task.yaml", tmp_path, raw, tmp_path / "work")
    response = FetchResult(CrawlRequest("https://example.org"), "https://example.org", 200,
                           {"content-type": "text/html"}, b"<h1>Right</h1><h2>Wrong</h2>", 0.1)
    return config, response


def test_empty_regression_is_not_verified(tmp_path):
    config, _ = setup_fixture(tmp_path)
    assert not verify_regression_fixtures(config)["ok"]


def test_saved_body_digest_is_enforced(tmp_path):
    config, response = setup_fixture(tmp_path)
    library = RegressionLibrary(config)
    manifest = library.capture(response, records=1, processor="html")
    metadata = json.loads(manifest.read_text(encoding="utf-8"))
    (library.directory / metadata["body"]).write_bytes(gzip.compress(b"modified"))
    with pytest.raises(ValueError, match="hash"):
        library.load()


def test_same_count_wrong_values_fail_and_baseline_is_not_auto_replaced(tmp_path):
    config, response = setup_fixture(tmp_path)
    library = RegressionLibrary(config)
    expected = HTMLProcessor(config).process(response).records
    assert expected and expected[0].data["title"] == "Right"
    path = library.capture(response, records=expected, processor="html")
    before = path.read_bytes()
    assert verify_regression_fixtures(config)["ok"]
    config.raw["extract"]["fields"]["title"]["selector"] = "h2"
    assert not verify_regression_fixtures(config)["ok"]
    library.capture(response, records=HTMLProcessor(config).process(response).records, processor="html")
    assert path.read_bytes() == before
    assert not verify_regression_fixtures(config)["ok"]


def test_count_only_legacy_baseline_is_explicitly_unverified(tmp_path):
    config, response = setup_fixture(tmp_path)
    RegressionLibrary(config).capture(response, records=1, processor="html")
    report = verify_regression_fixtures(config)
    assert not report["ok"]
    assert report["results"][0]["status"] == "unverified"


def test_corrupt_fixture_report_cannot_pass(tmp_path):
    config, response = setup_fixture(tmp_path)
    library = RegressionLibrary(config)
    manifest = library.capture(response, records=HTMLProcessor(config).process(response).records, processor="html")
    metadata = json.loads(manifest.read_text(encoding="utf-8"))
    (library.directory / metadata["body"]).write_bytes(b"not gzip")
    report = verify_regression_fixtures(config)
    assert not report["ok"]
    assert report["status"] == "invalid_fixture"


def test_changing_to_custom_extractor_does_not_verify_builtin_output(tmp_path):
    config, response = setup_fixture(tmp_path)
    RegressionLibrary(config).capture(response, records=HTMLProcessor(config).process(response).records, processor="html")
    config.raw["extract"]["extractor"] = "custom"
    assert verify_regression_fixtures(config)["results"][0]["status"] == "unverified"


def test_response_change_creates_separate_historical_input(tmp_path):
    config, response = setup_fixture(tmp_path)
    library = RegressionLibrary(config)
    first = library.capture(response, records=HTMLProcessor(config).process(response).records, processor="html")
    changed = FetchResult(response.request, response.final_url, 200, response.headers, b"<h1>New</h1>", 0.1)
    second = library.capture(changed, records=HTMLProcessor(config).process(changed).records, processor="html")
    assert first != second
    assert verify_regression_fixtures(config)["ok"]
    assert verify_regression_fixtures(config)["fixtures"] == 2
