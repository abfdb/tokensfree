"""Bounded attempt execution: admission-skips, retry before first byte only."""
import asyncio
from types import SimpleNamespace

import pytest

from tokensfree.adapters.base import NormalizedError
from tokensfree.router.admission import AdmissionDenied
from tokensfree.router.execute import Candidate, Execution, NoProviderAvailable, execute


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


def run(candidates, **kwargs):
    return asyncio.run(execute(candidates, admit=admit_all, request="request", **kwargs))


def test_first_candidate_succeeds():
    execution = run([Candidate(spec("alpha"), FakeAdapter(["hello"]))])
    assert isinstance(execution, Execution)
    assert execution.provider == "alpha"
    assert execution.attempts == (("alpha", "success"),)


def test_retryable_error_falls_back_to_next_candidate():
    candidates = [
        Candidate(spec("alpha"), FakeAdapter([NormalizedError("rate_limited", retryable=True)])),
        Candidate(spec("beta"), FakeAdapter(["hello"])),
    ]
    execution = run(candidates)
    assert execution.provider == "beta"
    assert [attempt[1] for attempt in execution.attempts] == ["rate_limited", "success"]


def test_content_scoped_error_is_final():
    candidates = [
        Candidate(spec("alpha"), FakeAdapter([NormalizedError("refusal", retryable=False)])),
        Candidate(spec("beta"), FakeAdapter(["never"])),
    ]
    with pytest.raises(NormalizedError) as caught:
        run(candidates)
    assert caught.value.code == "refusal"
    assert candidates[1].adapter.calls == 0


@pytest.mark.parametrize("code", ["auth_invalid", "quota_exhausted"])
def test_account_scoped_error_falls_through_to_the_next_account(code):
    candidates = [
        Candidate(spec("alpha"), FakeAdapter([NormalizedError(code, retryable=False)])),
        Candidate(spec("beta"), FakeAdapter(["hello"])),
    ]
    execution = run(candidates)
    assert execution.provider == "beta"
    assert execution.attempts[0] == ("alpha", code)


def test_failure_is_attributed_to_the_provider_spec():
    candidates = [Candidate(spec("alpha"), FakeAdapter([NormalizedError("auth_invalid", retryable=False)]))]
    with pytest.raises(NormalizedError) as caught:
        run(candidates)
    assert caught.value.provider == "alpha"


def test_admission_denied_is_skipped_without_calling_adapter():
    adapter = FakeAdapter(["unused"])
    candidates = [Candidate(spec("alpha"), adapter)]

    def deny(candidate_spec):
        raise AdmissionDenied("unverified_free_status")

    with pytest.raises(NoProviderAvailable) as caught:
        asyncio.run(execute(candidates, admit=deny, request="request"))
    assert adapter.calls == 0
    assert caught.value.attempts == (("alpha", "denied:unverified_free_status"),)


def test_attempt_budget_is_bounded():
    candidates = [
        Candidate(spec(name), FakeAdapter([NormalizedError("upstream_error", retryable=True)]))
        for name in ("alpha", "beta", "gamma")
    ]
    with pytest.raises(NormalizedError):
        run(candidates, max_attempts=2)
    assert len(candidates[2].adapter.outcomes) == 1  # third candidate untouched


def test_no_retry_after_first_byte():
    candidates = [
        Candidate(spec("alpha"), FakeAdapter([NormalizedError("upstream_error", retryable=True)])),
        Candidate(spec("beta"), FakeAdapter(["never"])),
    ]
    with pytest.raises(NormalizedError):
        run(candidates, first_byte_sent=lambda: True)


def test_retryable_error_surfaces_when_candidates_are_exhausted():
    candidates = [Candidate(spec("alpha"), FakeAdapter([NormalizedError("rate_limited", retryable=True)]))]
    with pytest.raises(NormalizedError) as caught:
        run(candidates)
    assert caught.value.code == "rate_limited"


def test_no_candidates_raises():
    with pytest.raises(NoProviderAvailable) as caught:
        run([])
    assert caught.value.attempts == ()
