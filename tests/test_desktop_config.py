"""Desktop config: defaults, validation, persistence and launch arguments."""
import json

import pytest

from tokensfree.desktop import config as desktop_config


def make_config(tmp_path, **overrides):
    values = {
        "port": 8766,
        "token": "T" * 32,
        "db_path": str(tmp_path / "state.db"),
        "mode": "mock",
        "loopback_port": 0,
    }
    values.update(overrides)
    return desktop_config.DesktopConfig(**values)


def test_defaults_and_token_generation(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENSFREE_DESKTOP_DIR", str(tmp_path / "appdir"))
    config = desktop_config.load_config()
    assert config.port == 8766
    assert config.mode == "mock"
    assert len(config.token) >= 24
    assert config.db_path == str(tmp_path / "appdir" / "state.db")


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "desktop.json"
    config = make_config(tmp_path, port=8767, mode="loopback", loopback_port=19000, autostart=True)
    desktop_config.save_config(config, path)
    loaded = desktop_config.load_config(path)
    assert loaded == config
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 1


def test_corrupt_config_falls_back_to_defaults(tmp_path):
    path = tmp_path / "desktop.json"
    path.write_text("not json", encoding="utf-8")
    config = desktop_config.load_config(path)
    assert config.port == 8766
    assert config.token


@pytest.mark.parametrize(
    "overrides",
    [
        {"port": 70000},
        {"port": -1},
        {"port": "8766"},
        {"mode": "bogus"},
        {"mode": "loopback", "loopback_port": 0},
        {"token": ""},
        {"db_path": ""},
    ],
)
def test_invalid_configs_are_rejected(tmp_path, overrides):
    with pytest.raises((ValueError, TypeError)):
        desktop_config.validate_config(make_config(tmp_path, **overrides))


def test_launch_spec_for_each_mode(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    mock_config = make_config(tmp_path)
    args, env, cwd = desktop_config.launch_spec(mock_config, python="py", root=root)
    assert args[:3] == ["py", "-m", "tokensfree"]
    assert "--real" not in args and "--loopback" not in args
    assert env["TOKENSFREE_LOCAL_TOKEN"] == mock_config.token
    assert env["PYTHONPATH"] == str(root)
    assert cwd == str(root)

    real_args, _, _ = desktop_config.launch_spec(make_config(tmp_path, mode="real"), python="py", root=root)
    assert real_args[-1] == "--real"

    loop_args, _, _ = desktop_config.launch_spec(
        make_config(tmp_path, mode="loopback", loopback_port=19000), python="py", root=root
    )
    assert loop_args[-2:] == ["--loopback", "19000"]


def test_launch_spec_for_profiles_mode(tmp_path):
    profiles_file = tmp_path / "providers.json"
    config = make_config(tmp_path, mode="profiles", profiles_path=str(profiles_file))
    args, env, _cwd = desktop_config.launch_spec(config, python="py", root=tmp_path)
    assert args[-2:] == ["--profiles", str(profiles_file)]
    assert env["TOKENSFREE_ENABLE_REAL"] == "1"
    assert "TOKENSFREE_ALLOW_USER_PAID" not in env

    paid = make_config(tmp_path, mode="profiles", allow_user_paid=True)
    _args, paid_env, _cwd = desktop_config.launch_spec(paid, python="py", root=tmp_path)
    assert paid_env["TOKENSFREE_ALLOW_USER_PAID"] == "1"
