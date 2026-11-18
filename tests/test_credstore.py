"""Windows Credential Manager store: roundtrip, cleanup and fail-closed behaviour."""
import uuid

import pytest

from tokensfree import credstore

pytestmark = pytest.mark.skipif(not credstore.is_supported(), reason="Windows Credential Manager only")

SECRET = "test-secret-0123456789abcdef"


@pytest.fixture()
def provider():
    name = "tf-test-" + uuid.uuid4().hex[:12]
    try:
        yield name
    finally:
        try:
            credstore.delete_secret(name)
        except credstore.CredentialError:
            pass


def test_roundtrip_and_delete(provider):
    target = credstore.save_secret(provider, SECRET)
    assert target == "tokensfree/" + provider + "/env-key"
    assert credstore.load_secret(provider) == SECRET
    assert credstore.delete_secret(provider) is True
    with pytest.raises(credstore.CredentialError) as caught:
        credstore.load_secret(provider)
    assert provider in str(caught.value)


def test_delete_missing_returns_false(provider):
    assert credstore.delete_secret(provider) is False


def test_missing_credential_is_reported(provider):
    with pytest.raises(credstore.CredentialError):
        credstore.load_secret(provider)


def test_alias_separates_entries(provider):
    credstore.save_secret(provider, SECRET, alias="second")
    try:
        assert credstore.load_secret(provider, alias="second") == SECRET
        with pytest.raises(credstore.CredentialError):
            credstore.load_secret(provider, alias="env-key")
    finally:
        credstore.delete_secret(provider, alias="second")


def test_invalid_names_are_rejected():
    with pytest.raises(ValueError):
        credstore.target_name("Bad Provider")
    with pytest.raises(ValueError):
        credstore.target_name("ok", alias="../escape")
    with pytest.raises(ValueError):
        credstore.save_secret("ok", "")


def test_oversized_secret_is_rejected(provider):
    with pytest.raises(ValueError):
        credstore.save_secret(provider, "x" * (credstore.MAX_BLOB_BYTES + 1))


def test_unsupported_platform_fails_closed(monkeypatch):
    monkeypatch.setattr(credstore, "is_supported", lambda: False)
    with pytest.raises(credstore.CredentialError) as caught:
        credstore.load_secret("zhipu")
    assert "only implemented for Windows" in str(caught.value)
