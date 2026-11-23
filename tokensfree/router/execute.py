"""Bounded attempt execution across admitted candidates.

Retry rules (fail-closed):
- a candidate that admission denies is skipped and never called;
- a retryable upstream error moves to the next candidate only while the client
  has received no byte;
- once a byte was emitted the error is final: providers are never stitched.
"""
import time
from dataclasses import dataclass

from tokensfree.adapters.base import NormalizedError
from tokensfree.router.admission import AdmissionDenied
from tokensfree.router.candidates import bind_auto_model

# Errors that say "this account cannot serve right now" but leave other candidates usable.
ACCOUNT_SCOPED_ERRORS = frozenset({"auth_invalid", "quota_exhausted", "rate_limited"})


class NoProviderAvailable(Exception):
    """No candidate could be admitted and attempted within budget."""

    def __init__(self, attempts):
        self.attempts = tuple(attempts)
        super().__init__("no_free_provider")


@dataclass(frozen=True)
class Candidate:
    spec: object
    adapter: object


@dataclass(frozen=True)
class Execution:
    result: object
    provider: str
    attempts: tuple


async def execute(candidates, *, admit, request, max_attempts=2, first_byte_sent=None, breaker_for=None, clock=time.time, on_error=None):
    """Try candidates in order. Raises the last NormalizedError or NoProviderAvailable.

    breaker_for(spec) may return a CircuitBreaker: an open circuit skips the
    candidate without calling its adapter, and outcomes feed the breaker back.
    """
    if type(max_attempts) is not int or max_attempts < 1:
        raise ValueError("max_attempts must be a positive int")
    trace = []
    last_error = None
    for candidate in candidates:
        if len(trace) >= max_attempts:
            break
        breaker = breaker_for(candidate.spec) if breaker_for is not None else None
        if breaker is not None and not breaker.allows(clock()):
            trace.append((candidate.spec.name, "circuit_open"))
            continue
        try:
            admission = admit(candidate.spec)
        except AdmissionDenied as denied:
            trace.append((candidate.spec.name, "denied:" + denied.reason))
            continue
        try:
            upstream_request = bind_auto_model(request, candidate.spec)
            result = await candidate.adapter.complete(upstream_request, admission)
        except NormalizedError as error:
            if breaker is not None:
                breaker.record_failure(clock())
            # Attribute the failure to the provider spec (multi-key needs the exact key),
            # not to the generic adapter name.
            error.provider = candidate.spec.name
            if on_error is not None:
                on_error(candidate.spec, error)
            trace.append((candidate.spec.name, error.code))
            last_error = error
            streaming_started = first_byte_sent is not None and first_byte_sent()
            if streaming_started or len(trace) >= max_attempts:
                raise
            if not error.retryable and error.code not in ACCOUNT_SCOPED_ERRORS:
                raise
            continue
        if breaker is not None:
            breaker.record_success(clock())
        trace.append((candidate.spec.name, "success"))
        return Execution(result=result, provider=candidate.spec.name, attempts=tuple(trace))
    if last_error is not None:
        raise last_error
    raise NoProviderAvailable(trace)
