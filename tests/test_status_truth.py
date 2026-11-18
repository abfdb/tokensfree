"""/api/status must describe the runtime truthfully."""
import asyncio
import json

from providers import ProviderSpec
from tokensfree.config import load_config
from tokensfree.edge.app_core import Services
from tokensfree.edge.backend import EdgeRequest, MemoryBackend
from tokensfree.router.admission import admit

TOKEN = "s" * 32
CONFIG_ON = load_config({"TOKENSFREE_ENABLE_REAL": "1"})
CONFIG_OFF = load_config({})
MOCK = ProviderSpec("alpha", "mock-a", "mock_zero_cost", 1_000_000)
REAL = ProviderSpec("zhipu", "glm-flash", "free_verified", 1_000_000, simulation=False)


def backend_for(spec, config, port=8765, *, storage=None, breakers=None):
    names = frozenset({spec.name})

    def gate(candidate):
        return admit(candidate, config, names, 100)

    services = Services(
        specs=(spec,),
        adapters={},
        admit=gate,
        authorized=names,
        idempotency=getattr(storage, "idempotency", None),
        ledger=getattr(storage, "ledger", None),
        events=getattr(storage, "events", None),
        breakers=breakers,
    )
    return MemoryBackend(config=config, services=services, port=port, token=TOKEN)


def status_of(backend):
    request = EdgeRequest(
        method="GET",
        path="/api/status",
        headers={"host": "127.0.0.1:" + str(backend.port), "authorization": "Bearer " + TOKEN},
    )
    response = asyncio.run(backend.handle(request))
    assert response.status == 200
    return json.loads(response.body)


def test_mock_runtime_reports_simulation():
    payload = status_of(backend_for(MOCK, CONFIG_OFF))
    assert payload["simulation"] is True
    assert payload["providers"][0]["state"] == "eligible"


def test_real_runtime_reports_no_simulation_when_admitted():
    payload = status_of(backend_for(REAL, CONFIG_ON))
    assert payload["simulation"] is False
    assert payload["providers"][0]["state"] == "eligible"


def test_real_provider_is_blocked_and_reported_when_gate_closed():
    payload = status_of(backend_for(REAL, CONFIG_OFF))
    assert payload["simulation"] is False
    assert payload["providers"][0]["state"] == "blocked"
    assert payload["providers"][0]["policy_reason"] == "real_provider_not_implemented"



def test_status_reports_persistence_flags():
    from tokensfree.runtime import Storage

    payload = status_of(backend_for(MOCK, CONFIG_OFF))
    assert payload["persistence"] == {"idempotency": False, "ledger": False, "events": False}

    storage = Storage.open(":memory:")
    try:
        payload = status_of(backend_for(MOCK, CONFIG_OFF, storage=storage))
        assert payload["persistence"] == {"idempotency": True, "ledger": True, "events": True}
    finally:
        storage.close()


def test_status_reports_breaker_state_without_leaking_secrets():
    from tokensfree.router.health import CircuitBreaker

    breaker = CircuitBreaker(threshold=1, open_s=60)
    breaker.record_failure(1000)
    payload = status_of(backend_for(MOCK, CONFIG_OFF, breakers={"alpha": breaker}))
    assert payload["breakers"]["alpha"]["state"] == "open"
    assert payload["breakers"]["alpha"]["fail_streak"] == 1
    assert TOKEN not in json.dumps(payload)


def test_status_breaker_snapshot_failure_is_contained():
    class BrokenBreaker:
        def snapshot(self):
            raise RuntimeError("boom")

    payload = status_of(backend_for(MOCK, CONFIG_OFF, breakers={"alpha": BrokenBreaker()}))
    assert payload["breakers"]["alpha"] == {"state": "unknown"}
