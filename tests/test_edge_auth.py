"""Edge auth/origin checks as pure functions (ported from the legacy server semantics)."""
import pytest

from tokensfree.edge.auth import allowed_hosts, bearer_token, origin_allowed, token_matches


def test_allowed_hosts_are_loopback_only():
    hosts = allowed_hosts(8765)
    assert hosts == frozenset({"127.0.0.1:8765", "localhost:8765"})


def test_host_must_match_exact_port():
    assert origin_allowed("127.0.0.1:8765", None, 8765) is True
    assert origin_allowed("localhost:8765", None, 8765) is True
    for bad in ("127.0.0.1:8766", "evil.example", "", "127.0.0.1"):
        assert origin_allowed(bad, None, 8765) is False


def test_cross_origin_rejected():
    assert origin_allowed("127.0.0.1:8765", "http://127.0.0.1:8765", 8765) is True
    assert origin_allowed("127.0.0.1:8765", "http://localhost:8765", 8765) is True
    for bad in ("https://evil.example", "http://127.0.0.1:8766", "null"):
        assert origin_allowed("127.0.0.1:8765", bad, 8765) is False


def test_token_comparison_is_strict():
    assert token_matches("abc", "abc") is True
    assert token_matches("abc", "abd") is False
    assert token_matches("abc", "") is False
    for bad in (None, 5, b"abc"):
        assert token_matches("abc", bad) is False
    for bad in (None, "", 5):
        assert token_matches(bad, "abc") is False


def test_bearer_parsing():
    assert bearer_token("Bearer abc") == "abc"
    assert bearer_token("bearer abc") == "abc"
    assert bearer_token("Bearer  abc ") == "abc"
    for bad in (None, "", "Basic abc", "Bearer", "Bearer "):
        assert bearer_token(bad) is None


def test_invalid_port_rejected():
    for port in (0, 65536, "8765", True):
        with pytest.raises(ValueError):
            allowed_hosts(port)
