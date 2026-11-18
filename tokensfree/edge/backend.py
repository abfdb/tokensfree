"""Backend contract.

A framework backend implements one method: put bytes in, get bytes out. All
policy (routes, auth, parsing, routing, retries, persistence) stays in the kernel;
MemoryBackend is the reference implementation the contract tests run against.
"""
import json
import uuid
from dataclasses import dataclass, replace

from tokensfree.edge.app_core import breaker_lookup, handle_chat
from tokensfree.edge.auth import bearer_token, origin_allowed, token_matches
from tokensfree.edge.models import models_payload
from tokensfree.edge.protocol import build_error
from tokensfree.edge.routes import allowed_methods, resolve
from tokensfree.edge.stream_core import StreamFailure, iter_frames, prepare_stream
from tokensfree.errors import ErrorCode

JSON_TYPE = "application/json; charset=utf-8"
SSE_TYPE = "text/event-stream; charset=utf-8"
BASE_HEADERS = {"cache-control": "no-store", "x-content-type-options": "nosniff"}


@dataclass(frozen=True)
class EdgeRequest:
    method: str
    path: str
    headers: dict
    body: bytes = b""


@dataclass(frozen=True)
class EdgeResponse:
    status: int
    headers: dict
    body: bytes = b""
    stream: object = None


class MemoryBackend:
    """Reference backend with no sockets, used by the contract tests."""

    def __init__(self, *, config, services, port, token=None):
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("invalid port")
        resolved = token if token is not None else config.local_token
        if not isinstance(resolved, str) or not resolved:
            raise ValueError("stable local token required")
        self.config = config
        self.services = services
        self.port = port
        self.token = resolved

    def _json(self, status, payload, extra_headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"content-type": JSON_TYPE, **BASE_HEADERS}
        if extra_headers:
            headers.update(extra_headers)
        return EdgeResponse(status=status, headers=headers, body=body)

    def _error(self, code, request_id, *, extra_headers=None):
        status, payload = build_error(code, request_id)
        return self._json(status, payload, extra_headers)

    def _host_allowed(self, request):
        return origin_allowed(request.headers.get("host"), request.headers.get("origin"), self.port)

    def _token_ok(self, request):
        return token_matches(self.token, bearer_token(request.headers.get("authorization")))

    def _status(self):
        providers = []
        for spec in self.services.specs:
            try:
                self.services.admit(spec)
                state, reason = "eligible", "admitted"
            except Exception as denied:  # admission denials carry a stable reason string
                state, reason = "blocked", getattr(denied, "reason", "denied")
            providers.append({"name": spec.name, "state": state, "policy_reason": reason})
        # Never claim simulation when a real provider is configured: after the gate opens
        # this field must tell the truth.
        simulation = all(getattr(spec, "simulation", True) for spec in self.services.specs)
        persistence = {
            "idempotency": self.services.idempotency is not None,
            "ledger": self.services.ledger is not None,
            "events": self.services.events is not None,
        }
        breakers = {}
        for name, breaker in (getattr(self.services, "breakers", None) or {}).items():
            snapshot = getattr(breaker, "snapshot", None)
            if not callable(snapshot):
                continue
            try:
                breakers[str(name)] = snapshot()
            except Exception:
                breakers[str(name)] = {"state": "unknown"}
        return {
            "simulation": simulation,
            "persistence": persistence,
            "breakers": breakers,
            "providers": providers,
        }

    async def _chat(self, request, request_id):
        try:
            payload = json.loads(request.body.decode("utf-8"))
        except (ValueError, UnicodeError, RecursionError):
            return self._error(ErrorCode.invalid_request, request_id)
        if isinstance(payload, dict) and payload.get("stream") is True:
            plan = prepare_stream(payload, services=self.services, request_id=request_id)
            if isinstance(plan, StreamFailure):
                return self._json(plan.status, plan.body)
            headers = {"content-type": SSE_TYPE, **BASE_HEADERS}
            stream = iter_frames(plan, admit=self.services.admit, breaker_for=breaker_lookup(self.services))
            return EdgeResponse(status=200, headers=headers, stream=stream)
        outcome = await handle_chat(
            payload,
            services=self.services,
            request_id=request_id,
            idempotency_key=request.headers.get("idempotency-key"),
        )
        return self._json(outcome.status, outcome.body)

    async def handle(self, request):
        if not isinstance(request, EdgeRequest):
            raise TypeError("request must be an EdgeRequest")
        request_id = "req-" + uuid.uuid4().hex
        response = await self._dispatch(request, request_id)
        headers = dict(response.headers)
        headers.setdefault("x-request-id", request_id)
        return replace(response, headers=headers)

    async def _dispatch(self, request, request_id):
        if not isinstance(request, EdgeRequest):
            raise TypeError("request must be an EdgeRequest")
        if not self._host_allowed(request):
            return self._error(ErrorCode.origin_rejected, request_id)
        route = resolve(request.method, request.path)
        if route is None:
            methods = allowed_methods(request.path)
            if methods:
                return self._error(
                    ErrorCode.method_not_allowed, request_id, extra_headers={"allow": ", ".join(methods)}
                )
            return self._error(ErrorCode.not_found, request_id)
        if not self._token_ok(request):
            return self._error(ErrorCode.unauthorized, request_id)
        if route.endpoint == "models":
            return self._json(200, models_payload(self.services.specs))
        if route.endpoint == "status":
            return self._json(200, self._status())
        if route.endpoint == "chat":
            return await self._chat(request, request_id)
        return self._error(ErrorCode.not_found, request_id)
