"""Rehearse the documented user journey against a real server process.

Starts `python -m tokensfree` twice with the same SQLite file and walks through
models -> chat -> idempotent replay -> status -> auth/host rejection -> access
log, then checks that the replay survives a restart. No network beyond loopback.

Run: python scripts/journey_smoke.py   (exit 0 = verified)
"""
import http.client
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOKEN = "j" * 32
PORT = 18767
PAYLOAD = {"model": "tokensfree-auto", "messages": [{"role": "user", "content": "journey"}]}
KEY = "journey-key-1"


def wait_port(port, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.25)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.05)
    return False


def call(method, path, *, body=None, headers=None, host=None, token=TOKEN):
    merged = {"Host": host or ("127.0.0.1:" + str(PORT))}
    if token is not None:
        merged["Authorization"] = "Bearer " + token
    merged.update(headers or {})
    connection = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
    try:
        connection.request(method, path, json.dumps(body).encode() if body is not None else None, merged)
        response = connection.getresponse()
        raw = response.read()
        result = (response.status, {name.lower(): value for name, value in response.getheaders()}, raw)
    finally:
        connection.close()
    return result


def start(db_path, env):
    process = subprocess.Popen(
        [sys.executable, "-m", "tokensfree", "--port", str(PORT), "--db", str(db_path)],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if not wait_port(PORT):
        process.kill()
        raise SystemExit("server did not start")
    return process


def stop(process):
    process.terminate()
    try:
        out, err = process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        out, err = process.communicate()
    return out or "", err or ""


def main():
    checks = {}
    with tempfile.TemporaryDirectory(prefix="tokensfree-journey-") as temp:
        db_path = pathlib.Path(temp) / "state.db"
        env = dict(os.environ)
        env["TOKENSFREE_LOCAL_TOKEN"] = TOKEN
        env["PYTHONPATH"] = str(ROOT)

        first = start(db_path, env)
        try:
            status, headers, body = call("GET", "/v1/models")
            models = json.loads(body)
            checks["models"] = (
                status == 200 and bool(models["data"]) and headers.get("x-request-id", "").startswith("req-")
            )

            status, headers, body = call("POST", "/v1/chat/completions", body=PAYLOAD, headers={"Idempotency-Key": KEY})
            first_chat = json.loads(body)
            checks["chat"] = status == 200 and first_chat["choices"][0]["message"]["content"]
            checks["request_id_correlates"] = headers["x-request-id"] == first_chat["id"]

            status, headers, body = call("POST", "/v1/chat/completions", body=PAYLOAD, headers={"Idempotency-Key": KEY})
            checks["replay_same_process"] = status == 200 and json.loads(body) == first_chat

            status, _, body = call("GET", "/api/status")
            status_payload = json.loads(body)
            checks["status"] = (
                status == 200
                and status_payload["persistence"] == {"idempotency": True, "ledger": True, "events": True}
                and status_payload["simulation"] is True
            )

            checks["unauthorized"] = call("GET", "/v1/models", token=None)[0] == 401
            checks["bad_host"] = call("GET", "/v1/models", host="evil.example")[0] == 403
        finally:
            _, first_err = stop(first)

        checks["access_log"] = '"event": "http_request"' in first_err and TOKEN not in first_err

        second = start(db_path, env)
        try:
            status, _, body = call("POST", "/v1/chat/completions", body=PAYLOAD, headers={"Idempotency-Key": KEY})
            checks["replay_after_restart"] = status == 200 and json.loads(body) == first_chat
        finally:
            stop(second)

    summary = {"verified": all(checks.values()), "checks": checks, "db": "temp sqlite file"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["verified"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
