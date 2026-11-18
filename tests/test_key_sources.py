"""API keys from files, and round-robin so no single key always pays for the first attempt."""
import asyncio

import pytest

from test_multi_key import CONFIG_ON, KEY_ONE, KEY_TWO, MODEL, PROVIDER, KeyAwareUpstream, env_for
from tokensfree.edge.app_core import handle_chat
from tokensfree.router.scheduling import RoundRobin
from tokensfree.runtime import build_real_runtime, real_keys_from_env


def test_round_robin_orders():
    rotation = RoundRobin()
    assert rotation.order(("a", "b", "c")) == ("a", "b", "c")
    assert rotation.order(("a", "b", "c")) == ("b", "c", "a")
    assert rotation.order(("a", "b", "c")) == ("c", "a", "b")
    assert rotation.order(()) == ()


def test_single_key_file(tmp_path):
    path = tmp_path / "zhipu.key"
    path.write_text(KEY_ONE + "\n", encoding="utf-8")
    assert real_keys_from_env({"TOKENSFREE_REAL_API_KEY_FILE": str(path)}) == (KEY_ONE,)


def test_multi_key_file(tmp_path):
    path = tmp_path / "keys.txt"
    path.write_text(KEY_ONE + "\n\n" + KEY_TWO + "\n", encoding="utf-8")
    assert real_keys_from_env({"TOKENSFREE_REAL_API_KEYS_FILE": str(path)}) == (KEY_ONE, KEY_TWO)


def test_key_sources_are_mutually_exclusive(tmp_path):
    path = tmp_path / "keys.txt"
    path.write_text(KEY_ONE, encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        real_keys_from_env({"TOKENSFREE_REAL_API_KEY": KEY_ONE, "TOKENSFREE_REAL_API_KEY_FILE": str(path)})
    assert "only one of" in str(caught.value)


def test_missing_key_file_fails_closed(tmp_path):
    with pytest.raises(ValueError) as caught:
        real_keys_from_env({"TOKENSFREE_REAL_API_KEY_FILE": str(tmp_path / "absent.key")})
    assert "cannot read" in str(caught.value)


def test_rotation_spreads_first_attempts_across_keys():
    upstream = KeyAwareUpstream()
    upstream.start()
    try:
        runtime = build_real_runtime(config=CONFIG_ON, env=env_for(upstream.port, KEY_ONE + "," + KEY_TWO), clock=lambda: 1000.0)
        payload = {"model": MODEL, "messages": [{"role": "user", "content": "hi"}]}
        first = asyncio.run(handle_chat(payload, services=runtime.services, request_id="req-rot-1"))
        second = asyncio.run(handle_chat(payload, services=runtime.services, request_id="req-rot-2"))
        assert first.status == 200 and first.provider == PROVIDER + "-2"
        assert second.status == 200 and second.provider == PROVIDER + "-2"
        key_one_calls = [call for call in upstream.calls if KEY_ONE in call]
        key_two_calls = [call for call in upstream.calls if KEY_TWO in call]
        assert len(key_one_calls) == 1          # second request starts on key two
        assert len(key_two_calls) == 2
    finally:
        upstream.stop()
