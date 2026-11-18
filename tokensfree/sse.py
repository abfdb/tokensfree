"""Server-Sent Events framing for OpenAI-shaped chat chunks.

Bytes in, bytes out: no HTTP framework, no upstream transport, no retries.
The encoder refuses any chunk after a terminal frame, so a finished stream
cannot be silently extended by another provider.
"""
import json

DONE = b"data: [DONE]\n\n"


def _frame(payload):
    return ("data: " + json.dumps(payload, ensure_ascii=False) + "\n\n").encode("utf-8")


def keepalive():
    return b": keep-alive\n\n"


class SseEncoder:
    def __init__(self, *, model="tokensfree-auto", request_id="", include_usage=False):
        self.model = model
        self.request_id = request_id
        self.include_usage = include_usage
        self._finished = False

    def chunk(self, delta, finish_reason=None, usage=None):
        if self._finished:
            raise RuntimeError("stream already finished")
        if not isinstance(delta, dict):
            raise TypeError("delta must be a dict")
        payload = {
            "id": self.request_id,
            "object": "chat.completion.chunk",
            "created": 0,
            "model": self.model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        if usage is not None:
            payload["usage"] = usage
        if finish_reason is not None:
            self._finished = True
        return _frame(payload)

    def error(self, code, request_id=None):
        if self._finished:
            raise RuntimeError("stream already finished")
        self._finished = True
        self._errored = True
        payload = {"error": {"code": str(code), "request_id": request_id or self.request_id}}
        return ("event: error\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n").encode("utf-8")

    def done(self):
        if getattr(self, "_errored", False):
            raise RuntimeError("stream already finished with an error")
        self._finished = True
        return DONE


class ToolCallAssembler:
    """Merges OpenAI incremental tool-call deltas into complete calls."""

    def __init__(self):
        self._calls = {}

    def add(self, fragments):
        if not isinstance(fragments, (list, tuple)):
            raise TypeError("fragments must be a list")
        for fragment in fragments:
            if not isinstance(fragment, dict):
                raise TypeError("fragment must be a dict")
            index = fragment.get("index")
            if type(index) is not int or index < 0:
                raise ValueError("invalid tool call index")
            call = self._calls.setdefault(index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
            call_id = fragment.get("id")
            if call_id:
                if call["id"] and call["id"] != call_id:
                    raise ValueError("tool call id changed")
                call["id"] = call_id
            function = fragment.get("function") or {}
            name = function.get("name")
            if name:
                if call["function"]["name"] and call["function"]["name"] != name:
                    raise ValueError("tool name changed")
                call["function"]["name"] = name
            arguments = function.get("arguments")
            if arguments is not None:
                if not isinstance(arguments, str):
                    raise ValueError("arguments must be a string fragment")
                call["function"]["arguments"] += arguments

    def finish(self):
        if not self._calls:
            raise ValueError("no tool calls")
        if sorted(self._calls) != list(range(len(self._calls))):
            raise ValueError("tool call indexes must be contiguous from zero")
        result = []
        for index in sorted(self._calls):
            call = self._calls[index]
            if not call["id"]:
                raise ValueError("missing tool call id")
            if not call["function"]["name"]:
                raise ValueError("missing tool call name")
            result.append(call)
        return tuple(result)
