"""Client model narrowing: auto lets the router choose; a specific id must match."""
import asyncio

from providers import ProviderSpec
from tokensfree.adapters.base import ChatResult, NormalizedError
from tokensfree.config import load_config
from tokensfree.edge.app_core import Services, handle_chat
from tokensfree.edge.stream_core import StreamFailure, StreamPlan, prepare_stream
from tokensfree.router.admission import admit
from tokensfree.router.candidates import AUTO_MODEL, specs_for_model
from tokensfree.runtime import build_mock_runtime

CONFIG = load_config({})
PAYLOAD = {"model": AUTO_MODEL, "messages": [{"role": "user", "content": "hi"}]}


def call(runtime, model, **overrides):
    payload = dict(PAYLOAD, model=model, **overrides)
    return asyncio.run(handle_chat(payload, services=runtime.services, request_id="req-model"))


def test_specs_for_model_resolution():
    specs = (
        ProviderSpec("alpha", "mock-a", "mock_zero_cost", 10 ** 9),
        ProviderSpec("beta", "mock-b", "mock_zero_cost", 10 ** 9),
    )
    assert specs_for_model(specs, AUTO_MODEL) == specs
    assert [spec.name for spec in specs_for_model(specs, "mock-b")] == ["beta"]
    assert specs_for_model(specs, "missing") == ()


def test_auto_model_uses_the_first_candidate():
    runtime = build_mock_runtime(config=CONFIG)
    outcome = call(runtime, AUTO_MODEL)
    assert outcome.status == 200
    assert outcome.provider == "mock-alpha"


def test_specific_model_selects_only_that_provider():
    runtime = build_mock_runtime(config=CONFIG)
    outcome = call(runtime, "mock-b")
    assert outcome.status == 200
    assert outcome.provider == "mock-beta"
    assert outcome.body["choices"][0]["message"]["content"] == "mock-beta response"


def test_unknown_model_is_rejected():
    runtime = build_mock_runtime(config=CONFIG)
    outcome = call(runtime, "no-such-model")
    assert outcome.status == 404
    assert outcome.body["error"]["code"] == "model_not_found"


def test_model_narrowing_applies_to_tool_requirements():
    runtime = build_mock_runtime(config=CONFIG)
    outcome = call(runtime, "mock-a", tools=[{"type": "function", "function": {"name": "read"}}])
    assert outcome.status == 400
    assert outcome.body["error"]["code"] == "unsupported_capability"


def test_stream_plan_narrows_to_the_requested_model():
    from tokensfree.runtime import build_loopback_streaming_runtime

    runtime = build_loopback_streaming_runtime(9, config=CONFIG)
    plan = prepare_stream(dict(PAYLOAD, model="fixture-model", stream=True), services=runtime.services, request_id="req-stream")
    assert isinstance(plan, StreamPlan)
    assert [spec.name for spec, _ in plan.candidates] == ["loopback"]
    failure = prepare_stream(dict(PAYLOAD, model="missing", stream=True), services=runtime.services, request_id="req-stream")
    assert isinstance(failure, StreamFailure)
    assert failure.status == 404
    assert failure.body["error"]["code"] == "model_not_found"


class FlakyAdapter:
    capabilities = frozenset({"text"})

    def __init__(self, outcome):
        self.outcome = outcome

    async def complete(self, request, admission):
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return ChatResult(content=self.outcome)


def test_duplicate_model_across_providers_keeps_fallback_order():
    specs = (
        ProviderSpec("alpha", "shared-model", "mock_zero_cost", 10 ** 9),
        ProviderSpec("beta", "shared-model", "mock_zero_cost", 10 ** 9),
    )
    adapters = {
        "alpha": FlakyAdapter(NormalizedError("rate_limited", retryable=True)),
        "beta": FlakyAdapter("from beta"),
    }
    names = frozenset({"alpha", "beta"})
    services = Services(
        specs=specs,
        adapters=adapters,
        admit=lambda spec: admit(spec, CONFIG, names, 100),
        authorized=names,
    )
    outcome = asyncio.run(handle_chat(dict(PAYLOAD, model="shared-model"), services=services, request_id="req-dup"))
    assert outcome.status == 200
    assert outcome.provider == "beta"
