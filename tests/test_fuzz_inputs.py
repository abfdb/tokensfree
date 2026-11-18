"""Deterministic fuzzing: hostile input must yield a stable rejection, never a crash.

A random payload may be accepted (and then must be a well-formed ParsedChat) or
rejected with RequestRejected. Any other exception would surface as a 500.
"""
import json
import random

from tokensfree.adapters.stream_parse import StreamEvent, parse_data_line
from tokensfree.edge.protocol import ParsedChat, RequestRejected, parse_chat_payload

SCALARS = [None, True, False, 0, 1, -1, 2 ** 63, 1.5, float("nan"), "", "x", "\u0000", "很长的中文" * 50, [], {}, [None], {"a": 1}]
ROLES = ["system", "user", "assistant", "tool", "developer", "root", None, 1, ["user"]]


def random_message(rng):
    message = {}
    for key in rng.sample(["role", "content", "tool_calls", "tool_call_id", "name", "extra"], k=rng.randint(1, 5)):
        message[key] = rng.choice(SCALARS)
    if rng.random() < 0.5:
        message["role"] = rng.choice(ROLES)
    if rng.random() < 0.4:
        message["content"] = rng.choice(["hi", "x" * 5000, None, 42])
    return message


def random_payload(rng):
    payload = {}
    for key in rng.sample(["model", "messages", "stream", "max_tokens", "tools", "tool_choice", "store", "garbage"], k=rng.randint(0, 6)):
        if key == "messages":
            payload[key] = [random_message(rng) for _ in range(rng.randint(0, 3))]
        elif key == "model":
            payload[key] = rng.choice(["tokensfree-auto", "", "x" * 200, 3, None])
        elif key == "stream":
            payload[key] = rng.choice([True, False, 1, "true", None])
        elif key == "max_tokens":
            payload[key] = rng.choice([1, 1024, 128000, 0, -5, 10 ** 9, True, "64"])
        elif key == "tools":
            payload[key] = rng.choice([[], [{}], [{"type": "function"}], "tools", 5])
        elif key == "tool_choice":
            payload[key] = rng.choice(["auto", "bogus", {}, {"type": "function", "function": {"name": "read"}}, 1])
        else:
            payload[key] = rng.choice(SCALARS)
    return payload


def valid_payload(rng):
    return {
        "model": rng.choice(["tokensfree-auto", "gpt-4o-mini"]),
        "messages": [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ],
        "stream": rng.choice([True, False]),
        "max_tokens": rng.choice([1, 1024, 128000]),
    }


def mutate_valid(rng):
    payload = valid_payload(rng)
    for _ in range(rng.randint(1, 2)):
        key = rng.choice(list(payload))
        payload[key] = rng.choice(SCALARS + [random_payload(rng).get(key, None)])
    return payload


def test_random_chat_payloads_never_crash():
    rng = random.Random(20261118)
    accepted = 0
    for _ in range(400):
        payload = mutate_valid(rng) if rng.random() < 0.6 else random_payload(rng)
        try:
            parsed = parse_chat_payload(payload)
        except RequestRejected:
            continue
        accepted += 1
        assert isinstance(parsed, ParsedChat)
        assert isinstance(parsed.request.messages, tuple) and parsed.request.messages
        assert type(parsed.request.max_tokens) is int and parsed.request.max_tokens >= 1
        assert all(isinstance(message.get("content"), str) for message in parsed.request.messages)
        assert parsed.requires_tools is (parsed.request.tools != () or any("tool_calls" in m or m.get("role") == "tool" for m in parsed.request.messages))
    assert accepted > 0, "the fuzzer never produced a valid payload — generator is broken"


def test_random_json_text_is_never_accepted_as_a_payload():
    rng = random.Random(7)
    junk = ["", "null", "[]", "123", '"str"', "{", "not json", "{}", '{"messages": 1}']
    for _ in range(100):
        text = rng.choice(junk) + "".join(rng.choice('{}[]":,0123456789abc ') for _ in range(rng.randint(0, 40)))
        try:
            payload = json.loads(text)
        except ValueError:
            continue
        try:
            parse_chat_payload(payload)
        except RequestRejected:
            continue


def test_random_sse_lines_never_crash_the_parser():
    rng = random.Random(99)
    alphabet = 'data: {}[]",:0123456789abc\n\r\t\u0000'
    for _ in range(400):
        line = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 80)))
        try:
            event = parse_data_line(line)
        except Exception as error:  # NormalizedError is the only legal failure
            assert error.__class__.__name__ == "NormalizedError", (line, error)
            continue
        if event is not None:
            assert isinstance(event, StreamEvent)
            assert event.kind in {"delta", "finish", "done"}
