from omnicrawler.runtime.live_concurrency import LiveConcurrency


def test_rate_limits_lower_admission_and_healthy_window_recovers_within_cap():
    control = LiveConcurrency(4)
    assert control.observe(0.2, rate_limited=True) == 3
    for _ in range(24):
        control.observe(0.2)
    assert control.current == 4
    assert control.audit[0]["reason"]
    for _ in range(30):
        control.observe(0.2, resource_pressure=True)
    assert control.current == 1
    assert all(1 <= entry["after"] <= 4 for entry in control.audit)


def test_disabled_controller_preserves_requested_admission():
    control = LiveConcurrency(3, enabled=False)
    assert control.observe(20, failed=True, resource_pressure=True) == 3
    assert control.audit == []
