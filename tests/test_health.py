"""Provider circuit breaker: closed -> open -> half-open, one probe at a time."""
import pytest

from tokensfree.router.health import CircuitBreaker


def breaker(**kwargs):
    return CircuitBreaker(**kwargs)


def trip(instance, now=100):
    for index in range(instance.threshold):
        instance.record_failure(now + index)
    return instance


def test_closed_allows_everything():
    instance = breaker()
    assert instance.allows(100) is True


def test_threshold_opens_the_circuit():
    instance = trip(breaker(threshold=3))
    assert instance.state == "open"
    assert instance.allows(110) is False   # still inside the 30s open window


def test_open_expires_into_a_single_probe():
    instance = trip(breaker(threshold=1, open_s=30))
    assert instance.allows(129) is False
    assert instance.allows(131) is True     # transitions to half_open and leases one probe
    assert instance.allows(131) is False    # a second concurrent probe is refused
    assert instance.state == "half_open"


def test_successful_probe_closes():
    instance = trip(breaker(threshold=1, open_s=1))
    assert instance.allows(102) is True
    instance.record_success(102)
    assert instance.state == "closed"
    assert instance.allows(102) is True


def test_failed_probe_reopens():
    instance = trip(breaker(threshold=1, open_s=10))
    assert instance.allows(111) is True
    instance.record_failure(111)
    assert instance.state == "open"
    assert instance.allows(115) is False
    assert instance.allows(121) is True


def test_success_resets_the_failure_streak():
    instance = breaker(threshold=3)
    instance.record_failure(100)
    instance.record_failure(101)
    instance.record_success(102)
    instance.record_failure(103)
    assert instance.state == "closed"


def test_window_counts_old_failures_out():
    instance = breaker(threshold=10, window_s=10)
    instance.record_failure(100)
    instance.record_failure(101)
    instance.record_success(112)   # starts a fresh window after the gap
    assert instance.window_fails == 0


def test_snapshot_is_plain_data():
    instance = trip(breaker(threshold=2))
    snapshot = instance.snapshot()
    assert snapshot["state"] == "open"
    assert snapshot["fail_streak"] == 2
    assert isinstance(snapshot["open_until"], float)


def test_invalid_configuration_rejected():
    with pytest.raises(ValueError):
        CircuitBreaker(threshold=0)
    with pytest.raises(ValueError):
        CircuitBreaker(open_s=0)
    with pytest.raises(ValueError):
        CircuitBreaker(window_s=-1)
