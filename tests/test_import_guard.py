"""Static egress guard: network imports stay inside adapters and the host."""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_guard():
    spec = importlib.util.spec_from_file_location("check_imports", ROOT / "scripts" / "check_imports.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_repository_has_no_network_imports_outside_allowed_paths():
    guard = load_guard()
    assert guard.scan(ROOT) == []


def test_guard_detects_a_router_that_opens_a_socket(tmp_path):
    package = tmp_path / "tokensfree" / "router"
    package.mkdir(parents=True)
    (package / "leak.py").write_text("import socket\nimport http.client\n", encoding="utf-8")
    guard = load_guard()
    violations = guard.scan(tmp_path)
    assert [entry[0] for entry in violations] == ["tokensfree/router/leak.py", "tokensfree/router/leak.py"]
    assert {entry[2] for entry in violations} == {"socket", "http.client"}


def test_guard_allows_adapters_and_host(tmp_path):
    adapters = tmp_path / "tokensfree" / "adapters"
    adapters.mkdir(parents=True)
    (adapters / "transport.py").write_text("import http.client\nimport ssl\n", encoding="utf-8")
    host = tmp_path / "tokensfree" / "edge"
    host.mkdir(parents=True)
    (host / "host.py").write_text("import asyncio\n", encoding="utf-8")
    guard = load_guard()
    assert guard.scan(tmp_path) == []



def test_guard_allows_the_desktop_supervisor_and_flags_other_desktop_modules(tmp_path):
    desktop = tmp_path / "tokensfree" / "desktop"
    desktop.mkdir(parents=True)
    (desktop / "process.py").write_text("import urllib.request\n", encoding="utf-8")
    (desktop / "config.py").write_text("import socket\n", encoding="utf-8")
    guard = load_guard()
    violations = guard.scan(tmp_path)
    assert [(entry[0], entry[2]) for entry in violations] == [("tokensfree/desktop/config.py", "socket")]
