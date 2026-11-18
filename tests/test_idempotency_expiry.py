"""A slow request may outlive its idempotency reservation; the response still counts."""
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
        return ChatResult(content="still fine")


class ExpiredReservationRepo:
    def reserve(self, *args, **kwargs):
        from tokensfree.store.repos import Reserve

        return Reserve("new")

    def complete(self, *args, **kwargs):
        raise KeyError("expired")

    def cancel(self, *args, **kwargs):
        raise AssertionError("no cancel expected")


def services(repository):
    names = frozenset({SPEC.name})
    return Services(
        specs=(SPEC,),
        adapters={SPEC.name: EchoAdapter()},
        admit=lambda spec: admit(spec, CONFIG, names, 100),
        authorized=names,
        idempotency=repository,
    )


def test_expired_reservation_does_not_fail_the_request():
    outcome = asyncio.run(handle_chat(PAYLOAD, services=services(ExpiredReservationRepo()), request_id="req-exp", idempotency_key="k-1"))
    assert outcome.status == 200
    assert outcome.body["choices"][0]["message"]["content"] == "still fine"
