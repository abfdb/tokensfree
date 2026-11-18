"""Circuit breaker integrated into attempt execution."""
import asyncio
from types import SimpleNamespace

import pytest

from tokensfree.adapters.base import NormalizedError
from tokensfree.router.execute import Candidate, execute
from tokensfree.router.health import CircuitBreaker


def spec(name):
    return SimpleNamespace(name=name, capabilities=frozenset({"text"}), simulation=True)


class FakeAdapter:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    async def complete(self, request, admission):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def admit_all(candidate_spec):
    return "admission"


def run(candidates, *, breakers=None, clock=None):
    kwargs = {"admit": admit_all, "request": "request"}
    if breakers is not None:
        kwargs["breaker_for"] = lambda candidate_spec: breakers.get(candidate_spec.name)
    if clock is not None:
        kwargs["clock"] = clock
    return asyncio.run(execute(candidates, **kwargs))


def test_open_breaker_skips_without_calling_the_adapter():
    breaker = CircuitBreaker(threshold=1, open_s=60)
    breaker.record_failure(1000)
    adapter = FakeAdapter(["never"])
    candidates = [Candidate(spec("alpha"), adapter), Candidate(spec("beta"), FakeAdapter(["ok"]))]

    execution = run(candidates, breakers={"alpha": breaker}, clock=lambda: 1001)
    assert execution.provider == "beta"
    assert adapter.calls == 0
    assert execution.attempts[0] == ("alpha", "circuit_open")


def test_success_closes_the_breaker():
    breaker = CircuitBreaker(threshold=1, open_s=60)
    breaker.record_failure(1000)
    run([Candidate(spec("alpha"), FakeAdapter(["ok"]))], breakers={"alpha": breaker}, clock=lambda: 1061)
    assert breaker.state == "closed"


def test_failures_trip_the_breaker_for_the_next_call():
    breaker = CircuitBreaker(threshold=1, open_s=60)
    first = FakeAdapter([NormalizedError("auth_invalid", retryable=False)])
    with pytest.raises(NormalizedError):
        run([Candidate(spec("alpha"), first)], breakers={"alpha": breaker}, clock=lambda: 1000)
    assert breaker.state == "open"

    second = FakeAdapter(["never"])
    candidates = [Candidate(spec("alpha"), second)]
    with pytest.raises(Exception):
        run(candidates, breakers={"alpha": breaker}, clock=lambda: 1001)
    assert second.calls == 0


def test_without_breakers_behaviour_is_unchanged():
    execution = run([Candidate(spec("alpha"), FakeAdapter(["ok"]))])
    assert execution.provider == "alpha"
