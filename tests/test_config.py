"""Startup config: immutable, explicit mapping, fail-closed switches."""
import dataclasses

import pytest

from tokensfree.config import load_config


def test_defaults_are_closed():
    config = load_config({})
    assert config.port == 8765
    assert config.enable_real is False
    assert config.local_token is None
    assert config.max_body_bytes == 262144


def test_real_calls_only_by_explicit_switch():
    assert load_config({"TOKENSFREE_ENABLE_REAL": "1"}).enable_real is True
    assert load_config({"TOKENSFREE_ENABLE_REAL": "0"}).enable_real is False
    for value in ("yes", "true", "2", ""):
        with pytest.raises(ValueError):
            load_config({"TOKENSFREE_ENABLE_REAL": value})


def test_port_bounds():
    assert load_config({"TOKENSFREE_PORT": "9000"}).port == 9000
    for value in ("0", "65536", "-1", "abc"):
        with pytest.raises(ValueError):
            load_config({"TOKENSFREE_PORT": value})


def test_local_token_shape():
    good = "a" * 32
    assert load_config({"TOKENSFREE_LOCAL_TOKEN": good}).local_token == good
    for value in ("short", "a" * 129, "has space" * 4, ""):
        with pytest.raises(ValueError):
            load_config({"TOKENSFREE_LOCAL_TOKEN": value})


def test_config_is_frozen():
    config = load_config({})
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.port = 1


def test_requires_explicit_mapping():
    with pytest.raises((ValueError, TypeError)):
        load_config(["TOKENSFREE_PORT=9000"])
