import sys
from types import SimpleNamespace

from omnicrawler.services.resource_sampling import ProcessTreeSampler


def test_descendants_count_once_and_exited_process_is_ignored(monkeypatch):
    class GoneError(Exception):
        pass

    class DeniedError(Exception):
        pass

    def process(pid, rss):
        return SimpleNamespace(pid=pid, create_time=lambda: float(pid), memory_info=lambda: SimpleNamespace(rss=rss))

    child = process(2, 700)
    grandchild = process(3, 900)
    vanished = process(4, 1)
    vanished.memory_info = lambda: (_ for _ in ()).throw(GoneError())
    owner = process(1, 100)
    owner.children = lambda recursive: [child, grandchild, child, vanished] if recursive else [child]
    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(Process=lambda _pid: owner, NoSuchProcess=GoneError, AccessDenied=DeniedError))
    sampler = ProcessTreeSampler()
    sampler.sample()
    assert sampler.peak == 1700 and sampler.complete
    child.memory_info = lambda: (_ for _ in ()).throw(DeniedError())
    sampler.sample()
    assert not sampler.complete and sampler.incomplete_samples == 1


def test_timer_samples_without_events_and_stops_on_failure(monkeypatch):
    import threading

    sampled = threading.Event()
    sampler = ProcessTreeSampler(interval=0.01)

    def sample():
        sampler.samples += 1
        if sampler.samples >= 2:
            sampled.set()

    monkeypatch.setattr(sampler, "sample", sample)
    try:
        with sampler:
            assert sampled.wait(1)
            raise ValueError("task failed")
    except ValueError:
        pass
    assert sampler._thread is not None and not sampler._thread.is_alive()
