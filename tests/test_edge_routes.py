"""Static route table: unknown paths and wrong methods are explicit, not implicit 404s."""
import pytest

from tokensfree.edge.routes import ROUTES, allowed_methods, resolve


def test_known_routes_resolve():
    assert resolve("GET", "/v1/models").endpoint == "models"
    assert resolve("POST", "/v1/chat/completions").endpoint == "chat"
    assert resolve("get", "/v1/models").endpoint == "models"


def test_unknown_path_and_method_return_none():
    assert resolve("GET", "/v1/unknown") is None
    assert resolve("DELETE", "/v1/models") is None
    assert resolve("GET", "/v1/chat/completions") is None


def test_allowed_methods_for_a_path():
    assert allowed_methods("/v1/chat/completions") == ("POST",)
    assert allowed_methods("/v1/models") == ("GET",)
    assert allowed_methods("/nope") == ()


def test_routes_are_immutable_and_unique():
    keys = [(route.method, route.path) for route in ROUTES]
    assert len(keys) == len(set(keys))
    with pytest.raises(Exception):
        ROUTES[0].path = "/other"


def test_invalid_arguments_rejected():
    for args in ((None, "/v1/models"), ("GET", None), (5, "/v1/models")):
        with pytest.raises((TypeError, AttributeError)):
            resolve(*args)
