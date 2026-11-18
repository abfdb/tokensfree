"""Application core: one chat request, no HTTP objects.

Composes parse -> idempotency -> candidates -> admission -> execute -> persist.
A backend only moves bytes; every policy and bookkeeping decision stays here.
"""
import hashlib
import json
import time
from dataclasses import dataclass

from tokensfree import obs

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
    rotation: object = None
    account_ids: object = None
    accounts: object = None


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
    rotation = getattr(services, "rotation", None)
    if rotation is not None and enriched:
        enriched = rotation.order(enriched)
    try:
        execution = await execute(
            enriched,
            admit=services.admit,
            request=parsed.request,
            breaker_for=breaker_lookup(services),
            on_error=account_failure_hook(services, request_id),
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
        tool_calls=getattr(execution.result, "tool_calls", ()),
    )
    return ChatOutcome(status=200, body=body, request_id=request_id, provider=execution.provider)


def safe_bookkeeping(step, *, request_id, outcome="bookkeeping_failed"):
    """Ledger/event/account writes are best-effort: a storage hiccup must not fail the answer."""
    try:
        step()
    except Exception:
        obs.log_event("bookkeeping_error", level="error", request_id=request_id, outcome=outcome)


def mark_account_failure(services, error, *, request_id):
    """Persist what an account-scoped upstream failure told us about that account."""
    account = account_for(services, error.provider)
    if account is None:
        return
    accounts = getattr(services, "accounts", None)
    if error.code == "quota_exhausted" and services.ledger is not None:
        services.ledger.platform_signal(
            account, provider=error.provider, request_id=request_id, detail="quota_exhausted"
        )
    elif error.code == "auth_invalid" and accounts is not None:
        accounts.set_status(account, "invalid")
    elif error.code == "rate_limited" and accounts is not None and error.retry_after_s:
        accounts.set_cooldown(account, time.time() + float(error.retry_after_s))


def account_failure_hook(services, request_id):
    """Per-attempt hook: persist account-scoped failures even if a later candidate serves."""

    def hook(spec, error):
        safe_bookkeeping(
            lambda: mark_account_failure(services, error, request_id=request_id),
            request_id=request_id,
            outcome="account_bookkeeping_failed",
        )

    return hook


def account_for(services, provider):
    mapping = getattr(services, "account_ids", None)
    if not mapping or provider is None:
        return None
    return mapping.get(provider)


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


def record_stream_result(services, *, request_id, started, provider=None, error_code=None, status=None):
    """Ledger/event row for a streaming request, mirroring _record for JSON responses."""
    outcome = ChatOutcome(
        status=status if status is not None else (200 if error_code is None else 502),
        body={"error": {"code": error_code}} if error_code is not None else {},
        request_id=request_id,
        provider=provider,
    )
    account = account_for(services, provider)
    safe_bookkeeping(
        lambda: _record(services, outcome, request_id=request_id, account_id=account, started=started),
        request_id=request_id,
        outcome="stream_bookkeeping_failed",
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
            replay = ChatOutcome(
                status=int(stored.get("status", 200)),
                body=stored.get("body", {}),
                request_id=request_id,
            )
            # A replay is free (no ledger row) but must stay visible to operators.
            if services.events is not None:
                safe_bookkeeping(
                    lambda: services.events.append(
                        request_id,
                        outcome="replay",
                        account_id=account_id if account_id is not None else account_for(
                            services, replay.body.get("model") if isinstance(replay.body, dict) else None
                        ),
                        latency_ms=int((time.time() - started) * 1000),
                        detail=json.dumps({"status": replay.status, "error": _error_code(replay.body)}, ensure_ascii=False),
                        at=started,
                    ),
                    request_id=request_id,
                    outcome="replay_bookkeeping_failed",
                )
            return replay
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
        def complete_reservation():
            repository.complete(
                idempotency_key,
                json.dumps({"status": outcome.status, "body": outcome.body}, ensure_ascii=False),
            )

        # A failed completion (expired reservation, storage hiccup) loses only the
        # replay record, never the response itself.
        safe_bookkeeping(complete_reservation, request_id=request_id, outcome="idempotency_complete_failed")
    recorded_account = account_id if account_id is not None else account_for(services, outcome.provider)
    safe_bookkeeping(
        lambda: _record(services, outcome, request_id=request_id, account_id=recorded_account, started=started),
        request_id=request_id,
        outcome="ledger_bookkeeping_failed",
    )
    return outcome
