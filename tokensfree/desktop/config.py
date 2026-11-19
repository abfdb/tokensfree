"""Desktop shell configuration.

The desktop shell is a convenience wrapper around the same local gateway:
it persists a small JSON config under the user profile, launches
`python -m tokensfree` as a child process and shows the browser console URL.

Only the local gateway token lives here (never a provider API key). The file
inherits the user-profile ACL, which is the same protection the state DB gets.
"""
import json
import os
import pathlib
import secrets
from dataclasses import dataclass, replace

APP_DIR_NAME = "tokensfree"
CONFIG_NAME = "desktop.json"
MODES = ("mock", "loopback", "real", "profiles")
PORT_MIN = 1
PORT_MAX = 65535
DEFAULT_PORT = 8766


def app_dir():
    override = os.environ.get("TOKENSFREE_DESKTOP_DIR")
    if override:
        return pathlib.Path(override)
    home = (
        os.environ.get("APPDATA")
        or os.environ.get("USERPROFILE")
        or os.environ.get("HOME")
        or str(pathlib.Path.cwd())
    )
    return pathlib.Path(home) / APP_DIR_NAME


def config_path():
    return app_dir() / CONFIG_NAME


def repo_root():
    return pathlib.Path(__file__).resolve().parents[2]


def default_token():
    return secrets.token_urlsafe(24)


def default_db_path():
    return app_dir() / "state.db"


def console_url(port):
    return "http://127.0.0.1:%d/" % int(port)


@dataclass(frozen=True)
class DesktopConfig:
    port: int = DEFAULT_PORT
    token: str = ""
    db_path: str = ""
    mode: str = "mock"
    loopback_port: int = 0
    autostart: bool = False
    minimize_to_tray: bool = True
    profiles_path: str = ""
    allow_user_paid: bool = False


def _validate_port(value, *, label, allow_zero):
    if type(value) is not int or value < (0 if allow_zero else PORT_MIN) or value > PORT_MAX:
        raise ValueError("%s must be an integer in %d..%d" % (label, 0 if allow_zero else PORT_MIN, PORT_MAX))
    return value


def validate_config(config):
    if not isinstance(config, DesktopConfig):
        raise TypeError("config must be a DesktopConfig")
    _validate_port(config.port, label="port", allow_zero=True)
    if config.mode not in MODES:
        raise ValueError("mode must be one of " + ", ".join(MODES))
    if config.mode == "loopback":
        if type(config.loopback_port) is not int or not PORT_MIN <= config.loopback_port <= PORT_MAX:
            raise ValueError("loopback mode needs a valid loopback_port")
    elif config.loopback_port != 0 and type(config.loopback_port) is not int:
        raise ValueError("loopback_port must be an int")
    if not isinstance(config.token, str) or not config.token:
        raise ValueError("token must be a non-empty string")
    if not isinstance(config.db_path, str) or not config.db_path:
        raise ValueError("db_path must be a non-empty string")
    return config


def load_config(path=None):
    path = pathlib.Path(path) if path is not None else config_path()
    data = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        if isinstance(raw, dict):
            data = raw
    config = DesktopConfig(
        port=data.get("port", DEFAULT_PORT),
        token=data.get("token") or default_token(),
        db_path=data.get("db_path") or str(default_db_path()),
        mode=data.get("mode", "mock"),
        loopback_port=data.get("loopback_port", 0),
        autostart=bool(data.get("autostart", False)),
        minimize_to_tray=bool(data.get("minimize_to_tray", True)),
        profiles_path=str(data.get("profiles_path") or ""),
        allow_user_paid=bool(data.get("allow_user_paid", False)),
    )
    return validate_config(config)


def save_config(config, path=None):
    validate_config(config)
    path = pathlib.Path(path) if path is not None else config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "port": config.port,
        "token": config.token,
        "db_path": config.db_path,
        "mode": config.mode,
        "loopback_port": config.loopback_port,
        "autostart": config.autostart,
        "minimize_to_tray": config.minimize_to_tray,
        "profiles_path": config.profiles_path,
        "allow_user_paid": config.allow_user_paid,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def with_updates(config, **changes):
    return validate_config(replace(config, **changes))


def ensure_console_token(config):
    """Return a config with a token (saving is the caller's decision)."""
    if config.token:
        return config
    return with_updates(config, token=default_token())


def launch_spec(config, *, python=None, root=None):
    """Return (arguments, environment) for the gateway child process."""
    import sys

    validate_config(config)
    python = python or sys.executable
    root = pathlib.Path(root) if root is not None else repo_root()
    args = [python, "-m", "tokensfree", "--port", str(config.port), "--db", config.db_path]
    env = dict(os.environ)
    env["TOKENSFREE_LOCAL_TOKEN"] = config.token
    env["PYTHONPATH"] = str(root)
    if config.mode == "loopback":
        args.extend(["--loopback", str(config.loopback_port)])
    elif config.mode == "real":
        args.append("--real")
    elif config.mode == "profiles":
        from tokensfree import profiles as provider_profiles

        args.extend(["--profiles", config.profiles_path or str(provider_profiles.default_profiles_path())])
        env["TOKENSFREE_ENABLE_REAL"] = "1"
        if config.allow_user_paid:
            env["TOKENSFREE_ALLOW_USER_PAID"] = "1"
    return args, env, str(root)
