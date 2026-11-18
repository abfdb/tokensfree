"""DSH-shaped request through the backend contract.

DSH sends stream:true, a developer-role prompt and 20+ tool definitions, then
replays a tool result. These tests pin that shape before the real DSH smoke runs.
"""
import asyncio
import json

from providers import ProviderSpec
from tokensfree.adapters.stream_parse import StreamEvent
from tokensfree.config import load_config
from tokensfree.edge.app_core import Services
from tokensfree.edge.backend import EdgeRequest, MemoryBackend
from tokensfree.router.admission import admit

TOKEN = "b" * 32
CONFIG = load_config({"TOKENSFREE_LOCAL_TOKEN": TOKEN})
SPEC = ProviderSpec("mock-tools", "mock-tools-1", "mock_zero_cost", 200, frozenset({"text", "stream", "tools"}))
CALL_ID = "call_dsh_read_1"


def dsh_tools(count=23):
    return [
        {"type": "function", "function": {"name": "tool_" + str(index), "description": "d", "parameters": {"type": "object", "properties": {}}}}
        for index in range(count)
    ]


class RecordingStreamAdapter:
    capabilities = frozenset({"text", "stream", "tools"})

    def __init__(self):
        self.requests = []

    async def stream(self, request, admission):
        self.requests.append(request)
        yield StreamEvent("delta", {"role": "assistant"})
        yield StreamEvent("delta", {"tool_calls": [{"index": 0, "id": CALL_ID, "type": "function", "function": {"name": "tool_7"}}]})
        yield StreamEvent("delta", {"tool_calls": [{"index": 0, "function": {"arguments": '{"file_path":'}}]})
        yield StreamEvent("delta", {"tool_calls": [{"index": 0, "function": {"arguments": '"a.txt"}'}}]})
        yield StreamEvent("finish", finish_reason="tool_calls")


def backend_with(adapter):
    names = frozenset({SPEC.name})

    def gate(provider_spec):
        return admit(provider_spec, CONFIG, names, 100)

    services = Services(specs=(SPEC,), adapters={SPEC.name: adapter}, admit=gate, authorized=names)
    return MemoryBackend(config=CONFIG, services=services, port=8765, token=TOKEN)


def post(backend, payload):
    headers = {"host": "127.0.0.1:8765", "authorization": "Bearer " + TOKEN, "content-type": "application/json"}
    request = EdgeRequest(method="POST", path="/v1/chat/completions", headers=headers, body=json.dumps(payload).encode())
    return asyncio.run(backend.handle(request))


def frames_of(response):
    async def collect():
        return [chunk async for chunk in response.stream]

    return [chunk.decode("utf-8") for chunk in asyncio.run(collect())]


def test_dsh_first_turn_forwards_tools_and_streams_tool_calls():
    adapter = RecordingStreamAdapter()
    backend = backend_with(adapter)
    payload = {
        "model": "tokensfree-auto",
        "stream": True,
        "messages": [
            {"role": "developer", "content": "You are DSH."},
            {"role": "user", "content": "read a.txt"},
        ],
        "tools": dsh_tools(),
        "tool_choice": "auto",
    }
    response = post(backend, payload)
    assert response.status == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    decoded = frames_of(response)  # consuming the stream is what runs the adapter
    forwarded = adapter.requests[0]
    assert len(forwarded.tools) == 23
    assert forwarded.messages[0]["role"] == "system"
    assert forwarded.tool_choice == "auto"
    assert forwarded.stream is True
    assert decoded[-1].strip() == "data: [DONE]"
    chunks = [json.loads(line[len("data: "):]) for line in decoded if line.startswith("data: {")]
    tool_fragments = [chunk["choices"][0]["delta"]["tool_calls"] for chunk in chunks if "tool_calls" in chunk["choices"][0]["delta"]]
    assert tool_fragments[0][0]["id"] == CALL_ID
    assert "".join(fragment[0]["function"].get("arguments", "") for fragment in tool_fragments) == '{"file_path":"a.txt"}'
    assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"


def test_dsh_second_turn_replays_tool_result():
    adapter = RecordingStreamAdapter()
    backend = backend_with(adapter)
    payload = {
        "model": "tokensfree-auto",
        "stream": True,
        "messages": [
            {"role": "developer", "content": "You are DSH."},
            {"role": "user", "content": "read a.txt"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": CALL_ID, "type": "function", "function": {"name": "tool_7", "arguments": '{"file_path":"a.txt"}'}}]},
            {"role": "tool", "tool_call_id": CALL_ID, "content": "file body"},
        ],
        "tools": dsh_tools(),
    }
    response = post(backend, payload)
    assert response.status == 200
    frames_of(response)  # consume so the adapter actually runs
    forwarded = adapter.requests[0]
    assert forwarded.messages[-1]["role"] == "tool"
    assert forwarded.messages[-1]["tool_call_id"] == CALL_ID
    assert forwarded.messages[-2]["tool_calls"][0]["id"] == CALL_ID


def test_dsh_request_without_streaming_support_is_json_400():
    text_only = ProviderSpec("text-only", "m", "mock_zero_cost", 200)
    names = frozenset({text_only.name})

    def gate(provider_spec):
        return admit(provider_spec, CONFIG, names, 100)

    services = Services(specs=(text_only,), adapters={text_only.name: RecordingStreamAdapter()}, admit=gate, authorized=names)
    backend = MemoryBackend(config=CONFIG, services=services, port=8765, token=TOKEN)
    response = post(backend, {"model": "tokensfree-auto", "stream": True,
                              "messages": [{"role": "user", "content": "x"}], "tools": dsh_tools(3)})
    assert response.status == 400
    assert response.headers["content-type"].startswith("application/json")
    assert json.loads(response.body)["error"]["code"] == "unsupported_capability"
