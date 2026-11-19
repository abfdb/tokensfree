"""Startup UX: real bound port for --port 0, clean failure when the port is taken."""
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def host_env(token="s" * 32, **overrides):
    env = dict(os.environ)
    env["TOKENSFREE_LOCAL_TOKEN"] = token
    env.update(overrides)
    return env


def test_port_in_use_fails_cleanly():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    try:
        result = subprocess.run(
            [sys.executable, "-m", "tokensfree", "--port", str(port)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            env=host_env(),
        )
    finally:
        listener.close()
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "cannot bind 127.0.0.1:" + str(port) in output
    assert "Traceback" not in output


def test_port_zero_announces_the_real_port():
    process = subprocess.Popen(
        [sys.executable, "-m", "tokensfree", "--port", "0"],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=host_env(),
    )
    lines = []

    def reader():
        for line in process.stdout:
            lines.append(line.strip())
            if "http://127.0.0.1:" in line:
                return

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    deadline = time.time() + 15
    try:
        while time.time() < deadline and not any("http://127.0.0.1:" in line for line in lines):
            if process.poll() is not None:
                break
            time.sleep(0.05)
        assert any("http://127.0.0.1:" in line for line in lines), lines
        match = re.search(r"http://127\.0\.0\.1:(\d+)", lines[-1])
        assert match and int(match.group(1)) > 0
        # the reported port really accepts connections
        connection = socket.create_connection(("127.0.0.1", int(match.group(1))), timeout=5)
        connection.close()
    finally:
        process.terminate()
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()


def test_port_in_use_fails_with_a_clean_message(tmp_path):
    import socket
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        env = {
            "TOKENSFREE_LOCAL_TOKEN": "H" * 32,
            "PYTHONIOENCODING": "utf-8",
            "PATH": __import__("os").environ.get("PATH", ""),
            "SYSTEMROOT": __import__("os").environ.get("SystemRoot", ""),
        }
        result = subprocess.run(
            [sys.executable, "-m", "tokensfree", "--port", str(port), "--db", str(tmp_path / "state.db")],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "cannot bind 127.0.0.1:%d" % port in combined
    assert "Traceback" not in combined
