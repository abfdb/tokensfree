"""Streaming edge core: provider chunks -> client SSE frames.

Terminal rules (fail-closed):
- before the first frame is emitted, a retryable error may move to the next candidate;
- after the first frame the error is final: emit one error event and stop;
- a finished stream can never be extended by another provider.
"""
import time
from dataclasses import dataclass

from tokensfree.adapters.base import NormalizedError
from tokensfree.edge.protocol import RequestRejected, build_error, parse_chat_payload
from tokensfree.errors import ErrorCode
from tokensfree.router.admission import AdmissionDenied
from tokensfree.router.candidates import build_candidates
from tokensfree.router.execute import NoProviderAvailable
from tokensfree.sse import SseEncoder


@dataclass(frozen=True)
class StreamFailure:
    status: int
    body: dict


@dataclass(frozen=True)
class StreamPlan:
    parsed: object
    candidates: tuple
    request_id: str


def prepare_stream(payload, *, services, request_id, developer_role_policy="map"):
    """Validate and select candidates. Returns StreamFailure or StreamPlan."""
    try:
        parsed = parse_chat_payload(payload, developer_role_policy=developer_role_policy)
    except RequestRejected as rejected:
        status, body = build_error(rejected.code, request_id)
        return StreamFailure(status, body)
    if not parsed.stream:
        status, body = build_error(ErrorCode.invalid_request, request_id)
        return StreamFailure(status, body)
    if parsed.requires_tools and not any("tools" in spec.capabilities for spec in services.specs):
        status, body = build_error(ErrorCode.unsupported_capability, request_id)
        return StreamFailure(status, body)
    configured = frozenset(spec.name for spec in services.specs)
    candidates = []
    for spec in build_candidates(services.specs, allowed=configured, required=parsed.capabilities):
        adapter = services.adapters.get(spec.name)
        if adapter is None or not hasattr(adapter, "stream"):
            continue
        candidates.append((spec, adapter))
    if not candidates:
        status, body = build_error(ErrorCode.unsupported_capability, request_id)
        return StreamFailure(status, body)
    return StreamPlan(parsed=parsed, candidates=tuple(candidates), request_id=request_id)


async def iter_frames(plan, *, admit, max_attempts=2, breaker_for=None, clock=time.time):
    """Yield response bytes. Raises NormalizedError or NoProviderAvailable.

    An open circuit skips its candidate without opening an upstream connection.
    """
    trace = []
    last_error = None
    for spec, adapter in plan.candidates:
        if len(trace) >= max_attempts:
            break
        breaker = breaker_for(spec) if breaker_for is not None else None
        if breaker is not None and not breaker.allows(clock()):
            trace.append((spec.name, "circuit_open"))
            continue
        try:
            admission = admit(spec)
        except AdmissionDenied as denied:
            trace.append((spec.name, "denied:" + denied.reason))
            continue
        encoder = SseEncoder(model=spec.name, request_id=plan.request_id)
        emitted = False
        first = True
        try:
            async for event in adapter.stream(plan.parsed.request, admission):
                if event.kind == "delta":
                    delta = dict(event.delta or {})
                    if first:
                        if "role" not in delta:
                            yield encoder.chunk({"role": "assistant"})
                        emitted = True
                        first = False
                    if delta:
                        yield encoder.chunk(delta)
                    continue
                if event.kind == "finish":
                    if breaker is not None:
                        breaker.record_success(clock())
                    if first:
                        yield encoder.chunk({"role": "assistant"})
                        emitted = True
                        first = False
                    yield encoder.chunk({}, finish_reason=event.finish_reason or "stop")
                    yield encoder.done()
                    return
                if event.kind == "done":
                    # Some upstreams end with [DONE] but no finish_reason chunk.
                    if first:
                        yield encoder.chunk({"role": "assistant"})
                        emitted = True
                        first = False
                    if breaker is not None:
                        breaker.record_success(clock())
                    yield encoder.chunk({}, finish_reason="stop")
                    yield encoder.done()
                    return
        except NormalizedError as error:
            if breaker is not None:
                breaker.record_failure(clock())
            if emitted:
                yield encoder.error(error.code)
                return
            trace.append((spec.name, error.code))
            last_error = error
            if not error.retryable or len(trace) >= max_attempts:
                raise
            continue
        # The adapter's stream ended without finish/[DONE]: treat it as truncated,
        # never as a successful end, and never fall through silently.
        if emitted:
            yield encoder.error("upstream_disconnected")
            return
        trace.append((spec.name, "upstream_disconnected"))
        last_error = NormalizedError("upstream_disconnected", retryable=True, provider=spec.name)
        if len(trace) >= max_attempts:
            raise last_error
    if last_error is not None:
        raise last_error
    raise NoProviderAvailable(trace)
