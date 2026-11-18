"""Circuit-breaker state survives a restart via the provider_health table."""
import asyncio
import json
import time

import pytest

from tokensfree.config import load_config
from tokensfree.edge.app_core import handle_chat
from tokensfree.edge.backend import EdgeRequest, MemoryBackend
from tokensfree.router.health import CircuitBreaker
from tokensfree.runtime import Storage, build_mock_runtime

CONFIG = load_config({"TOKENSFREE_LOCAL_TOKEN": "h" * 32})
PAYLOAD = {"model": "tokensfree-auto", "messages": [{"role": "user", "content": "hi"}]}


@pytest.fixture()
def storage():
    opened = Storage.open(":memory:")
    try:
        yield opened
    finally:
        opened.close()


def test_health_repo_roundtrip(storage):
    storage.health.save("alpha", {"state": "open", "fail_streak": 3, "window_fails": 3, "open_until": 1234.0})
    snapshot = storage.health.get("alpha")
    assert snapshot == {"state": "open", "fail_streak": 3, "window_fails": 3, "open_until": 1234.0}
    storage.health.save("alpha", {"state": "closed", "fail_streak": 0, "window_fails": 0, "open_until": None})
    assert storage.health.get("alpha")["state"] == "closed"


def test_breaker_restores_from_snapshot():
    breaker = CircuitBreaker.from_snapshot({"state": "open", "fail_streak": 2, "window_fails": 2, "open_until": 5000.0})
    assert breaker.state == "open"
    assert breaker.allows(1000) is False
    assert breaker.allows(5001) is True
    assert breaker.state == "half_open"


def test_breaker_snapshot_rejects_garbage():
    with pytest.raises(ValueError):
        CircuitBreaker.from_snapshot({"state": "exploded"})
    with pytest.raises(ValueError):
        CircuitBreaker.from_snapshot("nope")


def test_watch_callback_fires_on_state_change():
    changes = []
    breaker = CircuitBreaker(threshold=2, open_s=60)
    breaker.watch(lambda changed: changes.append(changed.snapshot()["state"]))
    breaker.record_failure(1000)
    breaker.record_failure(1001)
    assert changes[-1] == "open"
    breaker.record_success(1002)
    assert changes[-1] == "closed"


def test_open_circuit_survives_a_runtime_rebuild(storage):
    first = build_mock_runtime(config=CONFIG, storage=storage)
    breaker = first.services.breakers["mock-alpha"]
    now = time.time()
    for index in range(breaker.threshold):
        breaker.record_failure(now + index)
    assert breaker.state == "open"
    assert storage.health.get("mock-alpha")["state"] == "open"

    second = build_mock_runtime(config=CONFIG, storage=storage)
    assert second.services.breakers["mock-alpha"].state == "open"
    outcome = asyncio.run(handle_chat(PAYLOAD, services=second.services, request_id="req-health"))
    assert outcome.status == 200
    assert outcome.provider == "mock-beta"
    assert outcome.body["tokensfree"]["trace"][0] == ["mock-alpha", "circuit_open"]


def test_status_reports_the_restored_breaker(storage):
    first = build_mock_runtime(config=CONFIG, storage=storage)
    breaker = first.services.breakers["mock-alpha"]
    for index in range(breaker.threshold):
        breaker.record_failure(time.time() + index)
    second = build_mock_runtime(config=CONFIG, storage=storage)
    backend = MemoryBackend(config=CONFIG, services=second.services, port=8765, token="h" * 32)
    response = asyncio.run(
        backend.handle(
            EdgeRequest(
                method="GET",
                path="/api/status",
                headers={"host": "127.0.0.1:8765", "authorization": "Bearer " + "h" * 32},
            )
        )
    )
    payload = json.loads(response.body)
    assert payload["breakers"]["mock-alpha"]["state"] == "open"
