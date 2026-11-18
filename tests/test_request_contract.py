"""Response contract: request id on every error, estimated usage on success."""
from server import Gateway

PAYLOAD = {"model": "tokensfree-auto", "messages": [{"role": "user", "content": "你好"}]}


def test_success_has_estimated_usage():
    status, body = Gateway().run(dict(PAYLOAD))
    assert status == 200
    usage = body["usage"]
    assert set(usage) == {"prompt_tokens", "completion_tokens", "total_tokens", "estimated"}
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]
    assert usage["estimated"] is True
    assert usage["prompt_tokens"] > 0 and usage["completion_tokens"] > 0


def test_error_carries_request_id():
    gateway = Gateway()
    gateway.reset("exhausted")
    status, body = gateway.dispatch(dict(PAYLOAD), request_id="req-abc")
    assert status == 503
    assert body["error"]["request_id"] == "req-abc"


def test_request_id_is_unique_per_call():
    gateway = Gateway()
    ids = {gateway.dispatch(dict(PAYLOAD))[1]["id"] for _ in range(20)}
    assert len(ids) == 20
