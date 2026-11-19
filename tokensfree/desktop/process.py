"""Supervise the local gateway child process used by the desktop shell."""
import collections
import json
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request

from tokensfree.desktop.config import console_url, launch_spec

ANNOUNCE_RE = re.compile(r"http://127\.0\.0\.1:(\d+)")
MAX_LOG_LINES = 500


class GatewayController:
    """Start/stop the gateway and expose its status; safe to poll from a UI thread."""

    def __init__(self, config):
        self._config = config
        self._lock = threading.Lock()
        self._process = None
        self._port = None
        self._logs = collections.deque(maxlen=MAX_LOG_LINES)
        self._reader = None

    @property
    def config(self):
        return self._config

    @property
    def port(self):
        return self._port

    @property
    def running(self):
        process = self._process
        return process is not None and process.poll() is None

    @property
    def exit_code(self):
        process = self._process
        return None if process is None else process.poll()

    def logs(self, tail=None):
        lines = list(self._logs)
        return lines[-tail:] if tail else lines

    def clear_logs(self):
        self._logs.clear()

    def start(self, *, timeout=30.0):
        with self._lock:
            if self.running:
                raise RuntimeError("gateway is already running")
            args, env, cwd = launch_spec(self._config)
            self._logs.clear()
            self._port = None
            self._process = subprocess.Popen(
                args,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            self._reader = threading.Thread(target=self._pump, args=(self._process,), daemon=True)
            self._reader.start()
        if not self.wait_ready(timeout=timeout):
            self.stop()
            raise RuntimeError("gateway did not become ready in %.0fs" % timeout)
        return self._port

    def _pump(self, process):
        stream = process.stdout
        if stream is None:
            return
        token = self._config.token
        for line in stream:
            line = line.rstrip("\n")
            if token and token in line:
                line = line.replace(token, "[local-token]")
            self._logs.append(line)
            match = ANNOUNCE_RE.search(line)
            if match and self._port is None:
                self._port = int(match.group(1))

    def wait_ready(self, *, timeout=30.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._port is not None:
                try:
                    self.status()
                    return True
                except RuntimeError:
                    pass
            if not self.running:
                return False
            time.sleep(0.05)
        return False

    def _request(self, path):
        port = self._port
        if port is None:
            raise RuntimeError("gateway is not ready")
        request = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (port, path),
            headers={"Authorization": "Bearer " + self._config.token},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise RuntimeError("gateway request failed: %s" % error) from None

    def status(self):
        return self._request("/api/status")

    def models(self):
        payload = self._request("/v1/models")
        return payload.get("data", []) if isinstance(payload, dict) else []

    def console_url(self):
        return console_url(self._port) if self._port is not None else None

    def stop(self, *, timeout=10.0):
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        self._process = None
        self._port = None


def probe_endpoint(base_url, api_key=None, *, timeout=8.0):
    """GET {base_url}/models and return (models, latency_s); raises RuntimeError on failure."""
    import time as _time

    base = str(base_url or "").rstrip("/")
    if not base:
        raise RuntimeError("base_url is empty")
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    request = urllib.request.Request(base + "/models", headers=headers)
    started = _time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read(256 * 1024).decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise RuntimeError("HTTP %d from %s/models" % (error.code, base)) from None
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise RuntimeError("cannot reach %s: %s" % (base, error)) from None
    latency = round(_time.time() - started, 3)
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise RuntimeError("endpoint did not return an OpenAI-compatible model list")
    models = [str(item.get("id")) for item in data if isinstance(item, dict) and item.get("id")]
    return models, latency