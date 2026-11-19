"""Backend contract.

A framework backend implements one method: put bytes in, get bytes out. All
policy (routes, auth, parsing, routing, retries, persistence) stays in the kernel;
MemoryBackend is the reference implementation the contract tests run against.
"""
import asyncio
import json
import pathlib
import time
import uuid
from dataclasses import dataclass, replace

from tokensfree import __version__
from tokensfree import credstore, profiles as provider_profiles

from tokensfree.edge.app_core import account_failure_hook, breaker_lookup, handle_chat, record_stream_result
from tokensfree.edge.auth import bearer_token, origin_allowed, token_matches
from tokensfree.edge.models import models_payload
from tokensfree.edge.protocol import build_error
from tokensfree.edge.routes import allowed_methods, resolve
from tokensfree.edge.stream_core import StreamFailure, iter_frames, prepare_stream
from tokensfree.errors import ErrorCode

JSON_TYPE = "application/json; charset=utf-8"
WEB_TYPES = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "application/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
}
WEB_FILES = frozenset(WEB_TYPES)
WEB_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self';"
    " img-src 'self' data:; base-uri 'none'; form-action 'none'"
)
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

    def __init__(self, *, config, services, port, token=None, static_dir=None, profiles_path=None):
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("invalid port")
        resolved = token if token is not None else config.local_token
        if not isinstance(resolved, str) or not resolved:
            raise ValueError("stable local token required")
        if static_dir is not None:
            static_dir = pathlib.Path(static_dir)
            if not static_dir.is_dir():
                raise ValueError("static_dir must be an existing directory")
        self.config = config
        self.services = services
        self.port = port
        self.token = resolved
        self.static_dir = static_dir
        self.profiles_path = pathlib.Path(profiles_path) if profiles_path is not None else None
        self.started_at = time.time()

    def _json(self, status, payload, extra_headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"content-type": JSON_TYPE, **BASE_HEADERS}
        if extra_headers:
            headers.update(extra_headers)
        return EdgeResponse(status=status, headers=headers, body=body)

    def _error(self, code, request_id, *, extra_headers=None):
        status, payload = build_error(code, request_id)
        return self._json(status, payload, extra_headers)

    def _static_file(self, path, request_id):
        if self.static_dir is None:
            return self._error(ErrorCode.not_found, request_id)
        name = "index.html" if path == "/" else path[len("/web/"):]
        if name not in WEB_FILES:
            return self._error(ErrorCode.not_found, request_id)
        try:
            body = (self.static_dir / name).read_bytes()
        except OSError:
            return self._error(ErrorCode.not_found, request_id)
        headers = {"content-type": WEB_TYPES[name], **BASE_HEADERS}
        if name.endswith(".html"):
            headers["content-security-policy"] = WEB_CSP
        return EdgeResponse(status=200, headers=headers, body=body)

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
            providers.append(
                {
                    "name": spec.name,
                    "model": spec.model,
                    "simulation": bool(getattr(spec, "simulation", True)),
                    "cost_policy": getattr(spec, "cost_evidence", "unknown"),
                    "state": state,
                    "policy_reason": reason,
                    "evidence_expires_at": spec.evidence_expires_at,
                }
            )
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
        accounts = []
        account_ids = getattr(self.services, "account_ids", None) or {}
        accounts_repo = getattr(self.services, "accounts", None)
        for spec in self.services.specs:
            account_id = account_ids.get(spec.name)
            if account_id is None or accounts_repo is None:
                continue
            row = accounts_repo.get(account_id)
            if row is None:
                continue
            charged = 0
            if self.services.ledger is not None:
                charged = int(round(-self.services.ledger.balance(account_id, unit="request")))
            accounts.append(
                {
                    "provider": spec.name,
                    "status": row["status"],
                    "cooldown_until": row["cooldown_until"],
                    "requests_charged": charged,
                }
            )
        return {
            "version": __version__,
            "simulation": simulation,
            "uptime_s": round(time.time() - self.started_at, 1),
            "persistence": persistence,
            "breakers": breakers,
            "accounts": accounts,
            "providers": providers,
        }

    def _profile_payload(self, profile):
        evidence = dict(profile.evidence or {})
        try:
            key_present = bool(credstore.load_secret(profile.id))
        except Exception:
            key_present = False
        return {
            "id": profile.id,
            "name": profile.name,
            "base_url": profile.base_url,
            "model": profile.model,
            "cost_policy": profile.cost_policy,
            "capabilities": sorted(profile.capabilities),
            "evidence": evidence,
            "enabled": profile.enabled,
            "key_present": key_present,
        }

    async def _profiles(self, request, request_id):
        if not self._token_ok(request):
            return self._error(ErrorCode.unauthorized, request_id)
        if self.profiles_path is None:
            if request.method.upper() == "GET" and request.path == "/api/profiles":
                return self._json(200, {"ok": True, "enabled": False, "profiles": [], "path": None})
            return self._json(
                400, {"ok": False, "error": "profile management is disabled (start with --profiles)"}
            )
        try:
            if request.method.upper() == "GET" and request.path == "/api/profiles":
                profiles = provider_profiles.load_profiles(self.profiles_path)
                return self._json(
                    200,
                    {
                        "ok": True,
                        "enabled": True,
                        "path": str(self.profiles_path),
                        "profiles": [self._profile_payload(profile) for profile in profiles],
                    },
                )
            if request.method.upper() == "POST" and request.path == "/api/profiles":
                return self._save_profile(request)
            prefix = "/api/profiles/"
            if request.method.upper() == "DELETE" and request.path.startswith(prefix):
                return self._delete_profile(request.path[len(prefix):])
            if request.method.upper() == "POST" and request.path.startswith(prefix) and request.path.endswith("/test"):
                profile_id = request.path[len(prefix):-len("/test")]
                return await self._test_profile(profile_id)
            return self._error(ErrorCode.not_found, request_id)
        except provider_profiles.ProfileError as error:
            return self._json(400, {"ok": False, "error": str(error)})
        except Exception:
            return self._json(500, {"ok": False, "error": "internal_error"})

    def _save_profile(self, request):
        try:
            body = json.loads(request.body.decode("utf-8"))
        except (ValueError, UnicodeError):
            return self._json(400, {"ok": False, "error": "body must be a JSON object"})
        if not isinstance(body, dict):
            return self._json(400, {"ok": False, "error": "body must be a JSON object"})
        api_key = body.get("api_key")
        try:
            profile = provider_profiles.profile_from_form(
                profile_id=body.get("id") or None, form=body
            )
            profiles = provider_profiles.load_profiles(self.profiles_path)
            provider_profiles.save_profiles(
                provider_profiles.upsert_profile(profiles, profile), self.profiles_path
            )
            if isinstance(api_key, str) and api_key.strip():
                provider_profiles.save_key(profile.id, api_key.strip())
        except provider_profiles.ProfileError as error:
            return self._json(400, {"ok": False, "error": str(error)})
        except Exception as error:
            return self._json(400, {"ok": False, "error": type(error).__name__})
        return self._json(
            200,
            {
                "ok": True,
                "restart_required": True,
                "profile": self._profile_payload(profile),
            },
        )

    def _delete_profile(self, profile_id):
        if not provider_profiles.ID_RE.match(str(profile_id or "")):
            return self._json(400, {"ok": False, "error": "invalid profile id"})
        profiles = provider_profiles.load_profiles(self.profiles_path)
        remaining = provider_profiles.remove_profile(profiles, profile_id)
        if len(remaining) == len(profiles):
            return self._json(404, {"ok": False, "error": "profile_not_found"})
        provider_profiles.save_profiles(remaining, self.profiles_path)
        try:
            provider_profiles.delete_key(profile_id)
        except Exception:
            pass
        return self._json(200, {"ok": True, "restart_required": True})

    async def _test_profile(self, profile_id):
        profiles = provider_profiles.load_profiles(self.profiles_path)
        profile = next((item for item in profiles if item.id == profile_id), None)
        if profile is None:
            return self._json(404, {"ok": False, "error": "profile_not_found"})
        key = ""
        if profile.cost_policy != "zero_cost_local":
            try:
                key = provider_profiles.load_key(profile.id)
            except Exception:
                return self._json(400, {"ok": False, "error": "no_credential_for_profile"})
        if not key:
            key = "local-no-key"
        try:
            from tokensfree.adapters.openai_compat import OpenAICompatAdapter

            adapter = OpenAICompatAdapter(
                profile.base_url,
                key,
                timeout=15.0,
                allow_remote=True,
                allow_private_http=(profile.cost_policy == "zero_cost_local"),
            )
            started = time.time()
            models = await asyncio.to_thread(adapter.list_models)
        except Exception as error:
            code = getattr(error, "code", None)
            if code is None:
                return self._json(400, {"ok": False, "error": str(error)[:200]})
            return self._json(502, {"ok": False, "error": code})
        return self._json(
            200,
            {"ok": True, "models": models, "latency_s": round(time.time() - started, 3)},
        )

    async def _chat(self, request, request_id):
        try:
            payload = json.loads(request.body.decode("utf-8"))
        except (ValueError, UnicodeError, RecursionError):
            return self._error(ErrorCode.invalid_request, request_id)
        if isinstance(payload, dict) and payload.get("stream") is True:
            if request.headers.get("idempotency-key"):
                # A stream cannot be replayed byte-for-byte, so silently ignoring the
                # key would break the exactly-once promise. Refuse instead.
                return self._error(ErrorCode.invalid_request, request_id)
            started = time.time()
            plan = prepare_stream(payload, services=self.services, request_id=request_id)
            if isinstance(plan, StreamFailure):
                code = None
                if isinstance(plan.body, dict) and isinstance(plan.body.get("error"), dict):
                    code = plan.body["error"].get("code")
                record_stream_result(self.services, request_id=request_id, started=started, error_code=code, status=plan.status)
                return self._json(plan.status, plan.body)
            headers = {"content-type": SSE_TYPE, **BASE_HEADERS}

            def on_settled(provider, error_code):
                record_stream_result(
                    self.services,
                    request_id=request_id,
                    started=started,
                    provider=provider,
                    error_code=error_code,
                )

            stream = iter_frames(
                plan,
                admit=self.services.admit,
                breaker_for=breaker_lookup(self.services),
                on_error=account_failure_hook(self.services, request_id),
                on_settled=on_settled,
            )
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
        if request.path == "/" or request.path.startswith("/web/"):
            if request.method.upper() != "GET":
                return self._error(ErrorCode.method_not_allowed, request_id, extra_headers={"allow": "GET"})
            return self._static_file(request.path, request_id)
        if request.path == "/api/session" and self.static_dir is not None:
            if request.method.upper() != "GET":
                return self._error(ErrorCode.method_not_allowed, request_id, extra_headers={"allow": "GET"})
            # The browser console needs the local token before it can call the API.
            # This stays loopback-only (Host/Origin already validated) and mirrors the
            # legacy console; the token is not a provider key.
            return self._json(200, {"token": self.token, "base_url": "http://127.0.0.1:%d/v1" % self.port})
        if request.path == "/api/profiles" or request.path.startswith("/api/profiles/"):
            return await self._profiles(request, request_id)
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
