"""Candidate selection: deterministic order, filtering only, no policy decisions."""
from dataclasses import replace

import pytest

from providers import ProviderSpec
from tokensfree.router.candidates import build_candidates

A = ProviderSpec("alpha", "m-a", "mock_zero_cost", 200)
B = ProviderSpec("beta", "m-b", "mock_zero_cost", 200)
VISION = ProviderSpec("vision", "m-v", "mock_zero_cost", 200, frozenset({"text", "vision"}))


def test_keeps_configuration_order():
    result = build_candidates([A, B], allowed=frozenset({"alpha", "beta"}))
    assert [spec.name for spec in result] == ["alpha", "beta"]


def test_filters_unauthorized_providers():
    result = build_candidates([A, B], allowed=frozenset({"beta"}))
    assert [spec.name for spec in result] == ["beta"]


def test_filters_missing_capabilities():
    result = build_candidates([A, VISION], allowed=frozenset({"alpha", "vision"}), required=frozenset({"vision"}))
    assert [spec.name for spec in result] == ["vision"]


def test_is_pure_and_stable():
    first = build_candidates([A, B], allowed=frozenset({"alpha", "beta"}))
    second = build_candidates([A, B], allowed=frozenset({"alpha", "beta"}))
    assert first == second


def test_does_not_decide_free_status():
    paid = replace(A, cost_evidence="paid")
    result = build_candidates([paid], allowed=frozenset({"alpha"}))
    assert [spec.name for spec in result] == ["alpha"]


def test_rejects_wrong_types():
    with pytest.raises(TypeError):
        build_candidates([A], allowed=["alpha"])
    with pytest.raises(TypeError):
        build_candidates(["not-a-spec"], allowed=frozenset({"alpha"}))
