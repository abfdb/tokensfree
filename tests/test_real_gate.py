"""The real-provider gate: three switches must all hold before an adapter may run.

1) TOKENSFREE_ENABLE_REAL=1, 2) cost evidence == free_verified, 3) evidence not expired
   plus server-side authorization. Evidence validity is identical for mock and real.
"""
import asyncio
from dataclasses import replace

import pytest

from providers import ProviderSpec, evaluate
from tokensfree.adapters.base import ChatResult
from tokensfree.config import load_config
from tokensfree.edge.app_core import Services, handle_chat
from tokensfree.router.admission import AdmissionDenied, admit

REAL = ProviderSpec("zhipu", "glm-flash", "free_verified", 1_000_000, simulation=False)
MOCK = ProviderSpec("alpha", "mock-a", "mock_zero_cost", 1_000_000)
ON = load_config({"TOKENSFREE_ENABLE_REAL": "1"})
OFF = load_config({})


def admit_real(spec, config, authorized=frozenset({"zhipu"}), now=100):
    return admit(spec, config, authorized, now)


def test_switch_off_blocks_real_before_evidence():
    with pytest.raises(AdmissionDenied) as caught:
        admit_real(REAL, OFF)
    assert caught.value.reason == "real_provider_not_implemented"


def test_switch_on_without_evidence_is_rejected():
    weak = replace(REAL, cost_evidence="mock_zero_cost")
    with pytest.raises(AdmissionDenied) as caught:
        admit_real(weak, ON)
    assert caught.value.reason == "unverified_free_status"


def test_switch_on_with_expired_evidence_is_rejected():
    stale = replace(REAL, evidence_expires_at=50)
    with pytest.raises(AdmissionDenied) as caught:
        admit_real(stale, ON)
    assert caught.value.reason == "expired_free_evidence"


def test_all_switches_hold_admits_a_real_provider():
    admission = admit_real(REAL, ON)
    assert admission.provider == "zhipu"
    assert admission.simulation is False


def test_authorization_still_applies():
    with pytest.raises(AdmissionDenied) as caught:
        admit_real(REAL, ON, authorized=frozenset({"other"}))
    assert caught.value.reason == "not_authorized"


def test_legacy_evaluate_path_stays_closed():
    # Default evaluate() must keep blocking real providers even with perfect evidence.
    decision = evaluate(REAL, frozenset({"zhipu"}), 100)
    assert decision.allowed is False
    assert decision.reason == "real_provider_not_implemented"


class RecordingAdapter:
    capabilities = frozenset({"text"})

    def __init__(self):
        self.calls = 0

    async def complete(self, request, admission):
        self.calls += 1
        return ChatResult(content="real upstream answered")


def services(adapter, config, spec=REAL):
    names = frozenset({spec.name})

    def gate(candidate):
        return admit(candidate, config, names, 100)

    return Services(specs=(spec,), adapters={spec.name: adapter}, admit=gate, authorized=names)


def call(service):
    return asyncio.run(handle_chat({"model": "tokensfree-auto", "messages": [{"role": "user", "content": "hi"}]},
                                   services=service, request_id="req-real"))


def test_end_to_end_gate_closed_never_calls_the_adapter():
    adapter = RecordingAdapter()
    outcome = call(services(adapter, OFF))
    assert outcome.status == 503
    assert adapter.calls == 0


def test_end_to_end_gate_open_calls_the_adapter():
    adapter = RecordingAdapter()
    outcome = call(services(adapter, ON))
    assert outcome.status == 200
    assert outcome.provider == "zhipu"
    assert adapter.calls == 1
