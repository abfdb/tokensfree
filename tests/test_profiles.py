"""Provider profiles: validation rules, persistence and evidence resolution."""
import json

import pytest

from tokensfree import profiles as profiles_module
from tokensfree.profiles import (
    Profile,
    ProfileError,
    delete_key,
    key_ref_for,
    load_profiles,
    new_profile_id,
    remove_profile,
    resolve_evidence,
    save_key,
    save_profiles,
    upsert_profile,
    validate_profile,
)


def local_profile(**overrides):
    values = {
        "id": "local-ollama",
        "name": "本机 Ollama",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5:7b",
        "cost_policy": "zero_cost_local",
        "capabilities": frozenset({"text", "stream"}),
    }
    values.update(overrides)
    return Profile(**values)


def free_profile(**overrides):
    values = {
        "id": "zhipu-free",
        "name": "智谱免费额度",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4.7-flash",
        "cost_policy": "free_verified",
        "capabilities": frozenset({"text", "stream", "tools"}),
        "evidence": {"expires_at": 2_000_000_000},
    }
    values.update(overrides)
    return Profile(**values)


def test_local_profile_is_accepted():
    profile = validate_profile(local_profile())
    assert profile.base_url == "http://127.0.0.1:11434/v1"
    assert profile.capabilities == frozenset({"text", "stream"})


def test_loopback_hostname_and_private_addresses_are_local():
    for url in ("http://localhost:11434/v1", "http://192.168.1.20:8000/v1", "http://10.0.0.5:8080/v1"):
        validate_profile(local_profile(base_url=url))


def test_free_profile_requires_evidence():
    with pytest.raises(ProfileError):
        validate_profile(free_profile(evidence=None))
    with pytest.raises(ProfileError):
        validate_profile(free_profile(evidence={"snapshot": ""}))
    with pytest.raises(ProfileError):
        validate_profile(free_profile(evidence={"expires_at": -1}))


def test_plaintext_remote_endpoints_are_rejected():
    with pytest.raises(ProfileError):
        validate_profile(local_profile(base_url="http://api.example.com/v1"))


def test_user_paid_requires_https():
    with pytest.raises(ProfileError):
        validate_profile(
            Profile(
                id="paid",
                name="Paid",
                base_url="http://127.0.0.1:9999/v1",
                model="m",
                cost_policy="user_paid",
                capabilities=frozenset({"text"}),
            )
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"id": "Bad ID"},
        {"name": ""},
        {"model": ""},
        {"cost_policy": "bogus"},
        {"capabilities": frozenset({"stream"})},
        {"capabilities": frozenset({"text", "vision"})},
        {"base_url": "ftp://example.com/v1"},
        {"base_url": "https://example.com/v1?token=1"},
    ],
)
def test_invalid_profiles_are_rejected(overrides):
    with pytest.raises(ProfileError):
        validate_profile(free_profile(**overrides))


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "providers.json"
    profiles = [validate_profile(local_profile()), validate_profile(free_profile())]
    save_profiles(profiles, path)
    loaded = load_profiles(path)
    assert loaded == profiles
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == 1
    assert raw["profiles"][0]["key_ref"] == key_ref_for("local-ollama")
    assert "api-key" not in json.dumps(raw) or "sk-" not in json.dumps(raw)


def test_duplicate_ids_and_missing_files(tmp_path):
    path = tmp_path / "providers.json"
    with pytest.raises(ProfileError):
        save_profiles([validate_profile(local_profile()), validate_profile(local_profile())], path)
    assert load_profiles(path) == []


def test_corrupt_profiles_file_fails_closed(tmp_path):
    path = tmp_path / "providers.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ProfileError):
        load_profiles(path)
    path.write_text('{"profiles": "nope"}', encoding="utf-8")
    with pytest.raises(ProfileError):
        load_profiles(path)


def test_upsert_and_remove():
    first = validate_profile(local_profile())
    second = validate_profile(local_profile(id="second", name="Second"))
    profiles = upsert_profile([first], second)
    assert [profile.id for profile in profiles] == ["local-ollama", "second"]
    replaced = validate_profile(local_profile(name="Renamed"))
    profiles = upsert_profile(profiles, replaced)
    assert next(profile for profile in profiles if profile.id == "local-ollama").name == "Renamed"
    assert [profile.id for profile in remove_profile(profiles, "second")] == ["local-ollama"]
    generated = new_profile_id("我的 Ollama 服务")
    assert generated.startswith("ollama-")
    assert new_profile_id("!!") .startswith("profile-")


def test_resolve_evidence_inline_and_snapshot(tmp_path):
    inline = validate_profile(free_profile(evidence={"expires_at": 5000}))
    assert resolve_evidence(inline, clock=lambda: 1000) == 5000
    with pytest.raises(ProfileError):
        resolve_evidence(inline, clock=lambda: 6000)

    snapshot = tmp_path / "evidence.json"
    snapshot.write_text(
        json.dumps({"model": "glm-4.7-flash", "evidence_expires_at": 5000}), encoding="utf-8"
    )
    profile = validate_profile(free_profile(evidence={"snapshot": str(snapshot)}))
    assert resolve_evidence(profile, clock=lambda: 1000) == 5000

    snapshot.write_text(json.dumps({"model": "other-model", "evidence_expires_at": 5000}), encoding="utf-8")
    with pytest.raises(ProfileError):
        resolve_evidence(profile, clock=lambda: 1000)
    with pytest.raises(ProfileError):
        resolve_evidence(validate_profile(free_profile(evidence={"snapshot": str(tmp_path / "absent.json")})))


def test_credentials_never_go_into_the_profiles_file(tmp_path, monkeypatch):
    saved = {}

    def fake_save(profile_id, secret):
        saved[profile_id] = secret
        return key_ref_for(profile_id)

    def fake_delete(profile_id):
        saved.pop(profile_id, None)
        return True

    monkeypatch.setattr(profiles_module.credstore, "save_secret", fake_save)
    monkeypatch.setattr(profiles_module.credstore, "delete_secret", fake_delete)
    profile = validate_profile(free_profile())
    save_key(profile.id, "secret-value")
    assert saved == {profile.id: "secret-value"}
    path = tmp_path / "providers.json"
    save_profiles([profile], path)
    assert "secret-value" not in path.read_text(encoding="utf-8")
    delete_key(profile.id)
    assert saved == {}
