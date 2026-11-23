"""Wire regressions for model selection with the real OpenAI-compatible serializer."""
import asyncio
import io
import json
import unittest
from unittest.mock import patch

from providers import ProviderSpec
from tokensfree.adapters.base import NormalizedError
from tokensfree.adapters.openai_compat import OpenAICompatAdapter
from tokensfree.config import load_config
from tokensfree.edge.app_core import Services, handle_chat
from tokensfree.edge.stream_core import StreamPlan, iter_frames, prepare_stream
from tokensfree.router.admission import admit
from tokensfree.router.candidates import AUTO_MODEL

MESSAGE = {"role": "user", "content": "fixture prompt"}
REQUEST_ID = "req-auto-model-test"
CONFIG = load_config({"TOKENSFREE_ENABLE_REAL": "1"})
SSE_DELTA = b'data: {"choices":[{"delta":{"role":"assistant","content":"fixture output"}}]}\n\n'
SSE_DONE = b'data: [DONE]\n\n'


class FakeResponse(io.BytesIO):
    def __init__(self, status, body, disconnect=False):
        super().__init__(body)
        self.status = status
        self.disconnect = disconnect

    def getheader(self, name):
        return None

    def readline(self, size=-1):
        line = super().readline(size)
        if not line and self.disconnect:
            raise OSError("fixture disconnect")
        return line


class FakeConnection:
    def __init__(self, response, sent):
        self.response = response
        self.sent = sent

    def request(self, method, path, *, body, headers):
        assert (method, path) == ("POST", "/v1/chat/completions")
        payload = json.loads(body)
        self.sent.append(payload)
        assert headers["Accept"] == ("text/event-stream" if payload["stream"] else "application/json")

    def getresponse(self):
        return self.response

    def close(self):
        self.response.close()


class AutoModelUpstreamTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.responses = {}

        def connect(adapter):
            return FakeConnection(self.responses[adapter], self.sent)

        patcher = patch.object(OpenAICompatAdapter, "_connect", connect)
        patcher.start()
        self.addCleanup(patcher.stop)

    def services(self, first_status, *, stream, denied=False, disconnect=False):
        specs = tuple(
            ProviderSpec(name, model, "free_verified", 2000, frozenset({"text", "stream"}), simulation=False)
            for name, model in (("provider-a", "configured-model-a"), ("provider-b", "configured-model-b"))
        )
        adapters = {spec.name: OpenAICompatAdapter("http://127.0.0.1:1/v1", "fixture-only") for spec in specs}
        for spec in specs:
            first = spec is specs[0]
            status = first_status if first else 200
            truncated = disconnect and first
            if status != 200:
                body = b'{"error":{"code":"insufficient_quota"}}'
            elif stream:
                body = SSE_DELTA if truncated else SSE_DELTA + SSE_DONE
            else:
                body = b'{"choices":[{"message":{"content":"fixture output"},"finish_reason":"stop"}]}'
            self.responses[adapters[spec.name]] = FakeResponse(status, body, truncated)
        authorized = frozenset({"provider-b"} if denied else adapters)
        return Services(
            specs=specs, adapters=adapters, authorized=authorized,
            admit=lambda spec: admit(spec, CONFIG, authorized, 1000),
        )

    def send(self, services, *, stream, model=AUTO_MODEL):
        payload = {"model": model, "messages": [MESSAGE], "temperature": 0.25, "stream": stream}
        if not stream:
            outcome = asyncio.run(handle_chat(payload, services=services, request_id=REQUEST_ID))
            self.assertEqual(outcome.request_id, REQUEST_ID)
            return outcome
        plan = prepare_stream(payload, services=services, request_id=REQUEST_ID)
        self.assertIsInstance(plan, StreamPlan)

        async def collect():
            return b"".join([frame async for frame in iter_frames(plan, admit=services.admit)])

        body = asyncio.run(collect())
        self.assertEqual(plan.parsed.request.model, model)
        self.assertIn(REQUEST_ID.encode(), body)
        return body

    def check_sent(self, models, *, stream):
        self.assertEqual([p["model"] for p in self.sent], models)
        for payload in self.sent:
            self.assertIs(payload["stream"], stream)
            self.assertEqual(payload["messages"], [MESSAGE])
            self.assertEqual(payload["temperature"], 0.25)

    def test_each_auto_attempt_uses_its_own_model_and_quota_falls_back(self):
        for stream in (False, True):
            with self.subTest(stream=stream):
                self.sent.clear()
                result = self.send(self.services(429, stream=stream), stream=stream)
                self.check_sent(["configured-model-a", "configured-model-b"], stream=stream)
                if stream:
                    self.assertIn(b"fixture output", result)
                    self.assertIn(b"provider-b", result)
                    self.assertNotIn(b"provider-a", result)
                    self.assertTrue(result.endswith(SSE_DONE))
                else:
                    self.assertEqual(result.status, 200)
                    self.assertEqual(result.provider, "provider-b")
                    self.assertEqual(result.body["tokensfree"]["trace"], [
                        ["provider-a", "quota_exhausted"], ["provider-b", "success"],
                    ])

    def test_admission_denial_skips_upstream_and_explicit_model_stays_exact(self):
        for stream in (False, True):
            for denied, model in ((True, AUTO_MODEL), (False, "configured-model-b")):
                with self.subTest(stream=stream, denied=denied, model=model):
                    self.sent.clear()
                    self.send(self.services(200, stream=stream, denied=denied), stream=stream, model=model)
                    self.check_sent(["configured-model-b"], stream=stream)

    def test_specific_model_does_not_fall_back_to_a_different_model_on_quota(self):
        model = "configured-model-a"
        result = self.send(self.services(429, stream=False), stream=False, model=model)
        self.assertEqual(result.body["error"]["code"], "no_free_provider")
        self.check_sent([model], stream=False)

        self.sent.clear()
        with self.assertRaises(NormalizedError) as caught:
            self.send(self.services(429, stream=True), stream=True, model=model)
        self.assertEqual(caught.exception.code, "quota_exhausted")
        self.check_sent([model], stream=True)

    def test_streaming_disconnect_after_content_never_replays_to_next_model(self):
        result = self.send(self.services(200, stream=True, disconnect=True), stream=True)
        self.check_sent(["configured-model-a"], stream=True)
        self.assertIn(b"fixture output", result)
        self.assertIn(b"provider-a", result)
        self.assertIn(b"disconnected", result)
        self.assertNotIn(b"provider-b", result)
        self.assertNotIn(SSE_DONE, result)


if __name__ == "__main__":
    unittest.main()
