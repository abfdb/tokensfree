"""Models catalog: one source of truth for /v1/models and DSH static config."""
import pytest

from providers import ProviderSpec
from tokensfree.edge.models import models_payload

ALPHA = ProviderSpec("alpha", "mock-a", "mock_zero_cost", 200)
BETA = ProviderSpec("beta", "mock-b", "mock_zero_cost", 200, frozenset({"text", "vision"}))


def test_catalog_shape_and_order():
    payload = models_payload([ALPHA, BETA])
    assert payload["object"] == "list"
    assert [entry["id"] for entry in payload["data"]] == ["mock-a", "mock-b"]
    assert payload["data"][0]["owned_by"] == "alpha"
    assert payload["data"][0]["object"] == "model"


def test_capabilities_are_sorted_and_simulation_flagged():
    payload = models_payload([BETA])
    entry = payload["data"][0]
    assert entry["capabilities"] == ["text", "vision"]
    assert entry["simulation"] is True


def test_rejects_non_specs():
    with pytest.raises(TypeError):
        models_payload(["mock-a"])
