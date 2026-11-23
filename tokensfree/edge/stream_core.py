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
from tokensfree.router.candidates import bind_auto_model, build_candidates, specs_for_model
from tokensfree.router.execute import ACCOUNT_SCOPED_ERRORS, NoProviderAvailable
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
    eligible_specs = specs_for_model(services.specs, parsed.request.model)
    if not eligible_specs:
        status, body = build_error(ErrorCode.model_not_found, request_id)
        return StreamFailure(status, body)
    if parsed.requires_tools and not any("tools" in spec.capabilities for spec in eligible_specs):
        status, body = build_error(ErrorCode.unsupported_capability, request_id)
        return StreamFailure(status, body)
    configured = frozenset(spec.name for spec in eligible_specs)
    candidates = []
    for spec in build_candidates(eligible_specs, allowed=configured, required=parsed.capabilities):
        adapter = services.adapters.get(spec.name)
        if adapter is None or not hasattr(adapter, "stream"):
            continue
        candidates.append((spec, adapter))
    if not candidates:
        status, body = build_error(ErrorCode.unsupported_capability, request_id)
        return StreamFailure(status, body)
    rotation = getattr(services, "rotation", None)
    if rotation is not None:
        candidates = list(rotation.order(candidates))
    return StreamPlan(parsed=parsed, candidates=tuple(candidates), request_id=request_id)


async def iter_frames(plan, *, admit, max_attempts=2, breaker_for=None, clock=time.time, on_error=None, on_settled=None):
    """Yield response bytes. Raises NormalizedError or NoProviderAvailable.

    An open circuit skips its candidate without opening an upstream connection.
    on_settled(provider, error_code) fires exactly once with the terminal outcome
    so the backend can write ledger/event rows for streaming requests too.
    """
    trace = []
    last_error = None
    attempted = None
    settled = []

    def settle(provider_name, error_code):
        if settled:
            return
        settled.append(True)
        if on_settled is not None:
            on_settled(provider_name, error_code)
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
        attempted = spec.name
        encoder = SseEncoder(model=spec.name, request_id=plan.request_id)
        include_usage = bool(getattr(plan.parsed, "include_usage", False))
        usage = None
        emitted = False
        first = True
        finish_emitted = False
        try:
            upstream_request = bind_auto_model(plan.parsed.request, spec)
            async for event in adapter.stream(upstream_request, admission):
                if event.usage is not None:
                    usage = event.usage
                if event.kind == "delta":
                    if finish_emitted:
                        continue
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
                    if finish_emitted:
                        continue
                    if breaker is not None:
                        breaker.record_success(clock())
                    if first:
                        yield encoder.chunk({"role": "assistant"})
                        emitted = True
                        first = False
                    yield encoder.chunk({}, finish_reason=event.finish_reason or "stop")
                    finish_emitted = True
                    if not include_usage:
                        settle(spec.name, None)
                        yield encoder.done()
                        return
                    continue
                if event.kind == "done":
                    # Some upstreams end with [DONE] but no finish_reason chunk.
                    if not finish_emitted:
                        if first:
                            yield encoder.chunk({"role": "assistant"})
                            emitted = True
                            first = False
                        if breaker is not None:
                            breaker.record_success(clock())
                        yield encoder.chunk({}, finish_reason="stop")
                        finish_emitted = True
                    settle(spec.name, None)
                    if include_usage and usage is not None:
                        yield encoder.usage(usage)
                    yield encoder.done()
                    return
            if finish_emitted:
                # finish already reached; a missing [DONE] is tolerated once usage is delivered.
                settle(spec.name, None)
                if include_usage and usage is not None:
                    yield encoder.usage(usage)
                yield encoder.done()
                return
        except NormalizedError as error:
            if breaker is not None:
                breaker.record_failure(clock())
            error.provider = spec.name
            if on_error is not None:
                on_error(spec, error)
            if emitted:
                settle(spec.name, error.code)
                yield encoder.error(error.code)
                return
            trace.append((spec.name, error.code))
            last_error = error
            if len(trace) >= max_attempts:
                settle(spec.name, error.code)
                raise
            if not error.retryable and error.code not in ACCOUNT_SCOPED_ERRORS:
                settle(spec.name, error.code)
                raise
            continue
        # The adapter's stream ended without finish/[DONE]: treat it as truncated,
        # never as a successful end, and never fall through silently.
        if emitted:
            settle(spec.name, "upstream_disconnected")
            yield encoder.error("upstream_disconnected")
            return
        trace.append((spec.name, "upstream_disconnected"))
        last_error = NormalizedError("upstream_disconnected", retryable=True, provider=spec.name)
        if len(trace) >= max_attempts:
            settle(spec.name, "upstream_disconnected")
            raise last_error
    if last_error is not None:
        settle(attempted, last_error.code)
        raise last_error
    settle(attempted, "no_free_provider")
    raise NoProviderAvailable(trace)
