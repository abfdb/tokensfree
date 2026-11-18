"""Client-chosen idempotency keys must be bounded before they reach storage."""
import asyncio

from providers import ProviderSpec
from tokensfree.adapters.base import ChatResult
from tokensfree.config import load_config
from tokensfree.edge.app_core import Services, handle_chat
from tokensfree.router.admission import admit

CONFIG = load_config({})
SPEC = ProviderSpec("alpha", "mock-a", "mock_zero_cost", 200)
PAYLOAD = {"model": "tokensfree-auto", "messages": [{"role": "user", "content": "hi"}]}


class EchoAdapter:
    capabilities = frozenset({"text"})

    async def complete(self, request, admission):
        return ChatResult(content="ok")


class RecordingRepo:
    def __init__(self):
        self.reserve_calls = 0

    def reserve(self, *args, **kwargs):
        self.reserve_calls += 1
        raise AssertionError("storage must not be reached for an over-long key")

    def complete(self, *args, **kwargs):
        raise AssertionError("unreachable")


def services(repository):
    names = frozenset({SPEC.name})
    return Services(
        specs=(SPEC,),
        adapters={SPEC.name: EchoAdapter()},
        admit=lambda spec: admit(spec, CONFIG, names, 100),
        authorized=names,
        idempotency=repository,
    )


def test_over_long_idempotency_key_is_rejected_before_storage():
    repository = RecordingRepo()
    outcome = asyncio.run(handle_chat(PAYLOAD, services=services(repository), request_id="req-key", idempotency_key="k" * 5000))
    assert outcome.status == 400
    assert outcome.body["error"]["code"] == "invalid_request"
    assert repository.reserve_calls == 0


def test_empty_idempotency_key_is_rejected():
    repository = RecordingRepo()
    outcome = asyncio.run(handle_chat(PAYLOAD, services=services(repository), request_id="req-key", idempotency_key=""))
    assert outcome.status == 400
    assert repository.reserve_calls == 0
