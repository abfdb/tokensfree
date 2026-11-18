"""Application core: one chat request, no HTTP objects.

Composes parse -> idempotency -> candidates -> admission -> execute -> persist.
A backend only moves bytes; every policy and bookkeeping decision stays here.
"""
import hashlib
import json
import time
from dataclasses import dataclass

from tokensfree.adapters.base import NormalizedError
from tokensfree.edge.protocol import (
    RequestRejected,
    build_chat_response,
    build_error,
    map_upstream_error,
    parse_chat_payload,
)
from tokensfree.errors import ErrorCode
from tokensfree.router.candidates import build_candidates, specs_for_model
from tokensfree.router.execute import Candidate, NoProviderAvailable, execute

MAX_IDEMPOTENCY_KEY_CHARS = 200


@dataclass(frozen=True)
class Services:
    specs: tuple
    adapters: dict
    admit: object
    authorized: frozenset
    idempotency: object = None
    ledger: object = None
    events: object = None
    breakers: object = None


@dataclass(frozen=True)
class ChatOutcome:
    status: int
    body: dict
    request_id: str
    provider: str | None = None


def _estimate_usage(messages, content):
    prompt_chars = sum(len(str(message.get("content", ""))) for message in messages)
    prompt_tokens = max(1, prompt_chars // 4)
    completion_tokens = max(1, len(content) // 4)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "estimated": True,
    }


def breaker_lookup(services):
    """Return spec -> CircuitBreaker, or None when no breakers are configured."""
    mapping = getattr(services, "breakers", None)
    if not mapping:
        return None
    return lambda spec: mapping.get(spec.name)


def _fingerprint(payload):
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _error_code(body):
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return body["error"].get("code")
    return None


def _failed(code, request_id, *, provider=None, attempts=None):
    status, body = build_error(code, request_id)
    if attempts is not None:
        body["tokensfree"] = {"attempts": [list(attempt) for attempt in attempts]}
    return ChatOutcome(status=status, body=body, request_id=request_id, provider=provider)


async def _execute(payload, *, services, request_id, developer_role_policy):
    try:
        parsed = parse_chat_payload(payload, developer_role_policy=developer_role_policy)
    except RequestRejected as rejected:
        return _failed(rejected.code, request_id)

    # Fail closed instead of silently degrading: this core has no streaming edge yet.
    if parsed.stream:
        return _failed(ErrorCode.unsupported_capability, request_id)
    # Client model narrowing: "tokensfree-auto" lets the router choose, any other
    # id must name a configured model or the request is rejected.
    eligible_specs = specs_for_model(services.specs, parsed.request.model)
    if not eligible_specs:
        return _failed(ErrorCode.model_not_found, request_id)
    if parsed.requires_tools and not any("tools" in spec.capabilities for spec in eligible_specs):
        return _failed(ErrorCode.unsupported_capability, request_id)

    configured = frozenset(spec.name for spec in eligible_specs)
    candidates = build_candidates(eligible_specs, allowed=configured, required=parsed.capabilities)
    enriched = tuple(
        Candidate(spec=spec, adapter=services.adapters[spec.name])
        for spec in candidates
        if spec.name in services.adapters and hasattr(services.adapters[spec.name], "complete")
    )
    if candidates and not enriched:
        return _failed(ErrorCode.unsupported_capability, request_id)
    try:
        execution = await execute(
            enriched, admit=services.admit, request=parsed.request, breaker_for=breaker_lookup(services)
        )
    except NormalizedError as error:
        return _failed(map_upstream_error(error), request_id, provider=error.provider or None)
    except NoProviderAvailable as unavailable:
        return _failed(ErrorCode.no_free_provider, request_id, attempts=unavailable.attempts)

    usage = _estimate_usage(parsed.request.messages, execution.result.content)
    body = build_chat_response(
        request_id=request_id,
        model=execution.provider,
        content=execution.result.content,
        finish_reason=getattr(execution.result, "finish_reason", None) or "stop",
        usage=usage,
        trace=execution.attempts,
    )
    return ChatOutcome(status=200, body=body, request_id=request_id, provider=execution.provider)


def _record(services, outcome, *, request_id, account_id, started):
    detail = json.dumps({"status": outcome.status, "error": _error_code(outcome.body)}, ensure_ascii=False)
    if services.ledger is not None and account_id is not None:
        services.ledger.append(
            account_id,
            outcome.provider or "unknown",
            request_id,
            "request_attempt" if outcome.status == 200 else "request_failed",
            -1.0,
            "request",
            "estimated",
            detail=detail,
            now=started,
        )
    if services.events is not None:
        services.events.append(
            request_id,
            outcome="success" if outcome.status == 200 else "error",
            provider=outcome.provider,
            account_id=account_id,
            latency_ms=int((time.time() - started) * 1000),
            detail=detail,
            at=started,
        )


async def handle_chat(payload, *, services, request_id, developer_role_policy="map", idempotency_key=None, account_id=None):
    started = time.time()
    if idempotency_key is not None and (not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= MAX_IDEMPOTENCY_KEY_CHARS):
        return _failed(ErrorCode.invalid_request, request_id)
    repository = services.idempotency
    reserved = False
    if repository is not None and idempotency_key is not None:
        reservation = repository.reserve(idempotency_key, _fingerprint(payload), now=started)
        if reservation.status == "conflict":
            return _failed(ErrorCode.idempotency_conflict, request_id)
        if reservation.status == "capacity":
            return _failed(ErrorCode.idempotency_capacity, request_id)
        if reservation.status == "in_flight":
            return _failed(ErrorCode.idempotency_conflict, request_id)
        if reservation.status == "replay":
            stored = json.loads(reservation.response_json or "{}")
            return ChatOutcome(
                status=int(stored.get("status", 200)),
                body=stored.get("body", {}),
                request_id=request_id,
            )
        reserved = True

    try:
        outcome = await _execute(
            payload, services=services, request_id=request_id, developer_role_policy=developer_role_policy
        )
    except BaseException:
        if reserved:
            repository.cancel(idempotency_key)
        raise

    if reserved:
        try:
            repository.complete(
                idempotency_key,
                json.dumps({"status": outcome.status, "body": outcome.body}, ensure_ascii=False),
            )
        except KeyError:
            # The reservation expired (TTL) while this request was running. The
            # response is still valid; only the replay record is gone.
            pass
    _record(services, outcome, request_id=request_id, account_id=account_id, started=started)
    return outcome
