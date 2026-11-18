"""Application core: parse -> candidates -> admission -> execute -> response, no HTTP."""
import asyncio

from providers import ProviderSpec
from tokensfree.adapters.base import ChatResult, NormalizedError
from tokensfree.config import load_config
from tokensfree.edge.app_core import ChatOutcome, Services, handle_chat
from tokensfree.router.admission import admit

CONFIG = load_config({})
ALPHA = ProviderSpec("alpha", "m-a", "mock_zero_cost", 200)
BETA = ProviderSpec("beta", "m-b", "mock_zero_cost", 200)


class FakeAdapter:
    capabilities = frozenset({"text"})

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = 0

    async def complete(self, request, admission):
        self.calls += 1
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return ChatResult(content=self.outcome)


def services(*pairs, authorized=None):
    specs = tuple(spec for spec, _ in pairs)
    adapters = {spec.name: adapter for spec, adapter in pairs}
    names = frozenset(adapters) if authorized is None else authorized
    return Services(
        specs=specs,
        adapters=adapters,
        admit=lambda spec: admit(spec, CONFIG, frozenset(names), 100),
        authorized=frozenset(names),
    )


def call(payload, service, request_id="req-1", **kwargs):
    return asyncio.run(handle_chat(payload, services=service, request_id=request_id, **kwargs))


def payload(**overrides):
    base = {"model": "tokensfree-auto", "messages": [{"role": "user", "content": "hello"}]}
    base.update(overrides)
    return base


def test_success_end_to_end():
    adapter = FakeAdapter("hi there")
    outcome = call(payload(), services((ALPHA, adapter)))
    assert isinstance(outcome, ChatOutcome)
    assert outcome.status == 200
    assert outcome.provider == "alpha"
    assert outcome.body["choices"][0]["message"]["content"] == "hi there"
    assert outcome.body["tokensfree"]["trace"] == [["alpha", "success"]]
    assert adapter.calls == 1


def test_fallback_to_second_candidate():
    first = FakeAdapter(NormalizedError("rate_limited", retryable=True))
    second = FakeAdapter("from beta")
    outcome = call(payload(), services((ALPHA, first), (BETA, second)))
    assert outcome.status == 200
    assert outcome.provider == "beta"
    assert outcome.body["tokensfree"]["trace"] == [["alpha", "rate_limited"], ["beta", "success"]]


def test_stream_is_rejected_before_routing():
    adapter = FakeAdapter("never")
    outcome = call(payload(stream=True), services((ALPHA, adapter)))
    assert outcome.status == 400
    assert outcome.body["error"]["code"] == "unsupported_capability"
    assert adapter.calls == 0


def test_tools_request_without_tool_capable_provider_is_rejected():
    adapter = FakeAdapter("never")
    outcome = call(payload(tools=[{"type": "function", "function": {"name": "read"}}]), services((ALPHA, adapter)))
    assert outcome.status == 400
    assert outcome.body["error"]["code"] == "unsupported_capability"
    assert adapter.calls == 0


def test_invalid_payload_rejected():
    outcome = call({"model": "x"}, services((ALPHA, FakeAdapter("never"))))
    assert outcome.status == 400
    assert outcome.body["error"]["code"] == "invalid_request"
    assert outcome.body["error"]["request_id"] == "req-1"


def test_upstream_error_is_mapped_for_the_client():
    adapter = FakeAdapter(NormalizedError("auth_invalid", retryable=False, provider="alpha"))
    outcome = call(payload(), services((ALPHA, adapter)))
    assert outcome.status == 502
    assert outcome.body["error"]["code"] == "upstream_auth_failed"
    assert adapter.calls == 1


def test_all_denied_reports_no_free_provider_without_calls():
    adapter = FakeAdapter("never")
    service = services((ALPHA, adapter), authorized=frozenset())
    outcome = call(payload(), service)
    assert outcome.status == 503
    assert outcome.body["error"]["code"] == "no_free_provider"
    assert outcome.body["tokensfree"]["attempts"] == [["alpha", "denied:not_authorized"]]
    assert adapter.calls == 0



class LengthAdapter:
    capabilities = frozenset({"text"})

    async def complete(self, request, admission):
        from tokensfree.adapters.base import ChatResult

        return ChatResult(content="truncated", finish_reason="length")


def test_finish_reason_is_forwarded_to_the_client():
    spec = ProviderSpec("alpha", "mock-a", "mock_zero_cost", 200)
    services = type("S", (), {})()
    services.specs = (spec,)
    services.adapters = {"alpha": LengthAdapter()}
    services.authorized = frozenset({"alpha"})
    services.admit = lambda candidate: admit_state(candidate)
    services.idempotency = None
    services.ledger = None
    services.events = None
    services.breakers = None

    def admit_state(candidate):
        from tokensfree.router.admission import admit as real_admit

        return real_admit(candidate, load_config({}), frozenset({"alpha"}), 100)

    outcome = asyncio.run(handle_chat({"model": "tokensfree-auto", "messages": [{"role": "user", "content": "hi"}]},
                                      services=services, request_id="req-finish"))
    assert outcome.status == 200
    assert outcome.body["choices"][0]["finish_reason"] == "length"
