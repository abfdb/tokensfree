"""Application core: one chat request, no HTTP objects.

Composes parse -> candidates -> admission -> execute -> response. A backend only
moves bytes; every policy decision stays in this module and the kernel.
"""
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
from tokensfree.router.candidates import build_candidates
from tokensfree.router.execute import Candidate, NoProviderAvailable, execute


@dataclass(frozen=True)
class Services:
    specs: tuple
    adapters: dict
    admit: object
    authorized: frozenset


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


def _failed(code, request_id, *, provider=None, attempts=None):
    status, body = build_error(code, request_id)
    if attempts is not None:
        body["tokensfree"] = {"attempts": [list(attempt) for attempt in attempts]}
    return ChatOutcome(status=status, body=body, request_id=request_id, provider=provider)


async def handle_chat(payload, *, services, request_id, developer_role_policy="map"):
    try:
        parsed = parse_chat_payload(payload, developer_role_policy=developer_role_policy)
    except RequestRejected as rejected:
        return _failed(rejected.code, request_id)

    # Fail closed instead of silently degrading: this core has no streaming edge yet.
    if parsed.stream:
        return _failed(ErrorCode.unsupported_capability, request_id)
    if parsed.requires_tools and not any("tools" in spec.capabilities for spec in services.specs):
        return _failed(ErrorCode.unsupported_capability, request_id)

    # Client narrowing is not parsed yet: offer every configured spec and let admission decide.
    configured = frozenset(spec.name for spec in services.specs)
    candidates = build_candidates(services.specs, allowed=configured, required=parsed.capabilities)
    enriched = tuple(
        Candidate(spec=spec, adapter=services.adapters[spec.name])
        for spec in candidates
        if spec.name in services.adapters
    )
    try:
        execution = await execute(enriched, admit=services.admit, request=parsed.request)
    except NormalizedError as error:
        return _failed(map_upstream_error(error), request_id, provider=error.provider or None)
    except NoProviderAvailable as unavailable:
        return _failed(ErrorCode.no_free_provider, request_id, attempts=unavailable.attempts)

    usage = _estimate_usage(parsed.request.messages, execution.result.content)
    body = build_chat_response(
        request_id=request_id,
        model=execution.provider,
        content=execution.result.content,
        usage=usage,
        trace=execution.attempts,
    )
    return ChatOutcome(status=200, body=body, request_id=request_id, provider=execution.provider)
