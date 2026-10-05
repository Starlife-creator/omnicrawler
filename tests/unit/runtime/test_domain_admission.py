from omnicrawler.core.models import CrawlRequest
from omnicrawler.runtime.live_concurrency import DomainAdmission
from omnicrawler.state import StateStore


def test_rate_limited_host_does_not_reduce_healthy_hosts():
    admission = DomainAdmission(4)
    admission.observe(0.1, domain="slow", failed=True, rate_limited=True)
    assert admission.domain_limit("slow") == 3
    assert admission.domain_limit("healthy") == 4
    assert admission.current == 4
    for _ in range(32):
        admission.observe(2, domain="slow")
    assert admission.domain_limit("slow") == 4


def test_fair_claim_respects_domain_capacity_and_rotates(tmp_path):
    with StateStore(tmp_path / "state.sqlite3") as state:
        for index in range(20):
            state.enqueue(CrawlRequest(f"https://large.example/{index}"))
        state.enqueue(CrawlRequest("https://small.example/one"))
        def scope(url: str) -> str:
            return url.split("/")[2]
        first = state.claim(1, domain_scope=scope, domain_capacity=lambda _: 1)
        assert scope(first[0].url) == "large.example"
        second = state.claim(1, domain_scope=scope, domain_capacity=lambda _: 1)
        assert scope(second[0].url) == "small.example"
        blocked = state.claim(3, domain_scope=scope, domain_capacity=lambda _: 0)
        assert blocked == []
        assert state.pending_count() == 19
