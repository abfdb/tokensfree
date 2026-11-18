"""Shared test support: run an ASGI backend on a real loopback socket.

Not a test module (no test_ prefix): tests import HostRunner from here so the
threading/port handling lives in one place.
"""
import asyncio
import threading
import time

from tokensfree.edge.host import AsgiHost


class HostRunner:
    def __init__(self, app, *, max_body=262144):
        self.host = AsgiHost(app, port=0, max_body=max_body)
        self.thread = threading.Thread(target=lambda: asyncio.run(self.host.run_forever()), daemon=True)

    def start(self):
        self.thread.start()
        deadline = time.time() + 10
        while self.host.port == 0 and time.time() < deadline:
            time.sleep(0.01)
        assert self.host.port, "host did not start"
        return self.host.port

    def stop(self):
        self.host.stop()
        self.thread.join(10)
