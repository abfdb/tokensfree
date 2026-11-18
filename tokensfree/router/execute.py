"""Bounded attempt execution across admitted candidates.

Retry rules (fail-closed):
- a candidate that admission denies is skipped and never called;
- a retryable upstream error moves to the next candidate only while the client
  has received no byte;
- once a byte was emitted the error is final: providers are never stitched.
"""
from dataclasses import dataclass

from tokensfree.adapters.base import NormalizedError
from tokensfree.router.admission import AdmissionDenied


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


async def execute(candidates, *, admit, request, max_attempts=2, first_byte_sent=None):
    """Try candidates in order. Raises the last NormalizedError or NoProviderAvailable."""
    if type(max_attempts) is not int or max_attempts < 1:
        raise ValueError("max_attempts must be a positive int")
    trace = []
    last_error = None
    for candidate in candidates:
        if len(trace) >= max_attempts:
            break
        try:
            admission = admit(candidate.spec)
        except AdmissionDenied as denied:
            trace.append((candidate.spec.name, "denied:" + denied.reason))
            continue
        try:
            result = await candidate.adapter.complete(request, admission)
        except NormalizedError as error:
            trace.append((candidate.spec.name, error.code))
            last_error = error
            streaming_started = first_byte_sent is not None and first_byte_sent()
            if not error.retryable or streaming_started or len(trace) >= max_attempts:
                raise
            continue
        trace.append((candidate.spec.name, "success"))
        return Execution(result=result, provider=candidate.spec.name, attempts=tuple(trace))
    if last_error is not None:
        raise last_error
    raise NoProviderAvailable(trace)
