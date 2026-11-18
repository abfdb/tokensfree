"""The single gate: no adapter call exists without an Admission issued here."""
from dataclasses import replace

import pytest

from providers import ProviderSpec
from tokensfree.adapters.base import Admission
from tokensfree.config import load_config
from tokensfree.router.admission import AdmissionDenied, admit

SPEC = ProviderSpec("alpha", "m-a", "mock_zero_cost", 200)


def test_mock_provider_is_admitted():
    admission = admit(SPEC, load_config({}), frozenset({"alpha"}), 100)
    assert isinstance(admission, Admission)
    assert admission.provider == "alpha"
    assert admission.simulation is True


def test_unknown_free_status_denied():
    with pytest.raises(AdmissionDenied) as caught:
        admit(replace(SPEC, cost_evidence="unknown"), load_config({}), frozenset({"alpha"}), 100)
    assert caught.value.reason == "unverified_free_status"


def test_expired_evidence_denied():
    with pytest.raises(AdmissionDenied) as caught:
        admit(replace(SPEC, evidence_expires_at=100), load_config({}), frozenset({"alpha"}), 100)
    assert caught.value.reason == "expired_free_evidence"


def test_unauthorized_provider_denied():
    with pytest.raises(AdmissionDenied) as caught:
        admit(SPEC, load_config({}), frozenset({"other"}), 100)
    assert caught.value.reason == "not_authorized"


def test_real_provider_still_denied_with_switch_on():
    # P1 has no real adapter yet: enabling the switch must not open a path.
    config = load_config({"TOKENSFREE_ENABLE_REAL": "1"})
    with pytest.raises(AdmissionDenied) as caught:
        admit(replace(SPEC, simulation=False), config, frozenset({"alpha"}), 100)
    assert caught.value.reason == "real_provider_not_implemented"


def test_admission_denied_message_is_the_reason():
    with pytest.raises(AdmissionDenied) as caught:
        admit(SPEC, load_config({}), frozenset(), 100)
    assert str(caught.value) == "not_authorized"
