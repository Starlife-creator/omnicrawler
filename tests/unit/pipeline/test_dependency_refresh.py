"""Body refresh must terminate if the server still returns a bodyless response."""
from types import SimpleNamespace

import pytest

from omnicrawler.core.errors import ExtractionError
from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.pipeline._extract import _PipelineExtract


def test_dependency_refresh_cannot_loop_or_extract_another_304():
    request = CrawlRequest("https://example.test/item", headers={"If-None-Match":"old", "Authorization":"fixture"})
    result = FetchResult(request,request.url,304,{},b"",0)
    calls = []
    def fetch(run_id, refreshed):
        calls.append(refreshed)
        assert refreshed.fingerprint == request.fingerprint
        assert refreshed.headers == {"Authorization":"fixture"}
        assert refreshed.meta["_require_extraction_body"] is True
        return FetchResult(refreshed,refreshed.url,304,{},b"",0)
    pipeline = SimpleNamespace(_reuse_notification_observation=lambda *args:False, _fetch_checked=fetch)
    with pytest.raises(ExtractionError,match="仍未返回正文"):
        _PipelineExtract._handle_result(pipeline,"run",result,1,persist_response=False)
    assert len(calls) == 1
