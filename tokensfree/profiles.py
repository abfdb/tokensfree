"""User-defined OpenAI-compatible provider profiles ("接入通道").

A profile describes one endpoint + model the user wants to route through the
gateway: a local Ollama/LM Studio/vLLM server, a remote HTTPS OpenAI-compatible
API, or a free-tier provider with recorded evidence.

Keys never live in the profiles file: each profile references an entry in the
Windows Credential Manager (`tokensfree/<id>/api-key`). The file only carries
non-secret configuration, so it can be reviewed and backed up safely.

Cost policy protects the zero-bill default:
- free_verified: needs recorded free-tier evidence (snapshot or expiry);
- zero_cost_local: user's own compute; plaintext http is only allowed for
  loopback/private addresses, and the UI must confirm it is local compute;
- user_paid: disabled unless the operator explicitly allows it at runtime.
"""
import ipaddress
import json
import os
import pathlib
import re
import uuid
from dataclasses import dataclass

from tokensfree import credstore

PROFILES_VERSION = 1
COST_POLICIES = ("free_verified", "zero_cost_local", "user_paid")
CAPABILITY_NAMES = ("text", "stream", "tools")
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
MAX_PROFILES = 16


class ProfileError(ValueError):
    """Raised for invalid profile data; callers surface it to the operator."""


def default_profiles_path():
    appdata = os.environ.get("APPDATA") or os.environ.get("USERPROFILE") or os.environ.get("HOME")
    base = pathlib.Path(appdata) if appdata else pathlib.Path.cwd()
    return base / "tokensfree" / "providers.json"


def key_ref_for(profile_id):
    return "tokensfree/" + profile_id + "/env-key"


def new_profile_id(name):
    slug = re.sub(r"[^a-z0-9]+", "-", str(name or "").strip().lower()).strip("-")
    slug = slug[:24] or "profile"
    if not ID_RE.match(slug):
        slug = "profile"
    return slug + "-" + uuid.uuid4().hex[:6]


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    base_url: str
    model: str
    cost_policy: str = "free_verified"
    capabilities: frozenset = frozenset({"text"})
    evidence: dict = None  # noqa: RUF012 - frozen dataclass, replaced on updates
    enabled: bool = True


def _host_is_local(hostname):
    host = str(hostname).strip().lower()
    if host in LOCAL_HOSTS:
        return True
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return address.is_loopback or address.is_private


def _split_url(base_url):
    from urllib.parse import urlsplit

    parts = urlsplit(str(base_url or ""))
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ProfileError("base_url must be an http(s) URL with a host")
    if parts.query or parts.fragment:
        raise ProfileError("base_url must not carry query or fragment")
    return parts


def _validate_evidence(evidence):
    if not isinstance(evidence, dict) or not evidence:
        raise ProfileError("free_verified profiles need evidence (snapshot or expires_at)")
    snapshot = evidence.get("snapshot")
    expires_at = evidence.get("expires_at")
    if snapshot is not None:
        if not isinstance(snapshot, str) or not snapshot.strip():
            raise ProfileError("evidence.snapshot must be a file path")
        return {"snapshot": snapshot.strip()}
    if type(expires_at) in (int, float) and not isinstance(expires_at, bool) and expires_at > 0:
        return {"expires_at": float(expires_at)}
    raise ProfileError("evidence must contain snapshot or a positive expires_at")


def validate_profile(profile):
    if not isinstance(profile, Profile):
        raise ProfileError("profiles must be Profile instances")
    if not ID_RE.match(str(profile.id or "")):
        raise ProfileError("profile id must match [a-z0-9][a-z0-9-]{0,31}")
    name = str(profile.name or "").strip()
    if not name or len(name) > 64:
        raise ProfileError("profile name must be 1..64 characters")
    model = str(profile.model or "").strip()
    if not model or len(model) > 128:
        raise ProfileError("model must be 1..128 characters")
    if profile.cost_policy not in COST_POLICIES:
        raise ProfileError("cost_policy must be one of " + ", ".join(COST_POLICIES))
    capabilities = frozenset(profile.capabilities or ())
    if "text" not in capabilities or capabilities - frozenset(CAPABILITY_NAMES):
        raise ProfileError("capabilities must include text and only use " + ", ".join(CAPABILITY_NAMES))
    parts = _split_url(profile.base_url)
    local = _host_is_local(parts.hostname)
    if parts.scheme == "http" and not local:
        raise ProfileError("plaintext http endpoints are only allowed for loopback/private hosts")
    evidence = None
    if profile.cost_policy == "free_verified":
        evidence = _validate_evidence(profile.evidence)
        if not isinstance(profile.enabled, bool):
            raise ProfileError("enabled must be a bool")
    elif profile.cost_policy == "zero_cost_local" and not local and parts.scheme != "https":
        raise ProfileError("zero_cost_local endpoints must be local/private or use https")
    if profile.cost_policy == "user_paid" and parts.scheme != "https":
        raise ProfileError("user_paid endpoints must use https")
    return Profile(
        id=profile.id,
        name=name,
        base_url=str(profile.base_url).strip().rstrip("/"),
        model=model,
        cost_policy=profile.cost_policy,
        capabilities=capabilities,
        evidence=evidence,
        enabled=bool(profile.enabled),
    )


def _serialize(profile):
    payload = {
        "id": profile.id,
        "name": profile.name,
        "base_url": profile.base_url,
        "model": profile.model,
        "cost_policy": profile.cost_policy,
        "capabilities": sorted(profile.capabilities),
        "key_ref": key_ref_for(profile.id),
        "enabled": profile.enabled,
    }
    if profile.evidence:
        payload["evidence"] = dict(profile.evidence)
    return payload


def _deserialize(item):
    if not isinstance(item, dict):
        raise ProfileError("each profile must be a JSON object")
    capabilities = item.get("capabilities", ["text"])
    profile = Profile(
        id=str(item.get("id", "")),
        name=str(item.get("name", "")),
        base_url=str(item.get("base_url", "")),
        model=str(item.get("model", "")),
        cost_policy=str(item.get("cost_policy", "free_verified")),
        capabilities=frozenset(capabilities) if isinstance(capabilities, list) else frozenset(),
        evidence=item.get("evidence") if isinstance(item.get("evidence"), dict) else None,
        enabled=bool(item.get("enabled", True)),
    )
    return validate_profile(profile)


def load_profiles(path=None):
    path = pathlib.Path(path) if path is not None else default_profiles_path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ProfileError("cannot read profiles file: %s" % error) from None
    if not isinstance(raw, dict) or not isinstance(raw.get("profiles"), list):
        raise ProfileError("profiles file must be an object with a profiles list")
    items = raw["profiles"]
    if len(items) > MAX_PROFILES:
        raise ProfileError("at most %d profiles are supported" % MAX_PROFILES)
    profiles = [_deserialize(item) for item in items]
    ids = [profile.id for profile in profiles]
    if len(set(ids)) != len(ids):
        raise ProfileError("profile ids must be unique")
    return profiles


def save_profiles(profiles, path=None):
    path = pathlib.Path(path) if path is not None else default_profiles_path()
    validated = [validate_profile(profile) for profile in profiles]
    if len(validated) > MAX_PROFILES:
        raise ProfileError("at most %d profiles are supported" % MAX_PROFILES)
    ids = [profile.id for profile in validated]
    if len(set(ids)) != len(ids):
        raise ProfileError("profile ids must be unique")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": PROFILES_VERSION, "profiles": [_serialize(profile) for profile in validated]}
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return path


def upsert_profile(profiles, profile):
    profile = validate_profile(profile)
    kept = [existing for existing in profiles if existing.id != profile.id]
    for index, existing in enumerate(kept):
        if existing.id == profile.id:
            kept[index] = profile
    kept.append(profile)
    return kept


def remove_profile(profiles, profile_id):
    return [profile for profile in profiles if profile.id != profile_id]


def save_key(profile_id, secret):
    if not ID_RE.match(str(profile_id or "")):
        raise ProfileError("invalid profile id")
    if not isinstance(secret, str) or not secret:
        raise ProfileError("secret must be a non-empty string")
    return credstore.save_secret(profile_id, secret)


def delete_key(profile_id):
    if not ID_RE.match(str(profile_id or "")):
        raise ProfileError("invalid profile id")
    return credstore.delete_secret(profile_id)


def load_key(profile_id, *, loader=None):
    if loader is not None:
        return loader(profile_id)
    if not credstore.is_supported():
        raise ProfileError("system credential store is only implemented on Windows")
    return credstore.load_secret(profile_id)


def resolve_evidence(profile, *, clock=None, snapshot_reader=None):
    """Return the absolute expiry of a free_verified profile, validating bindings."""
    import time

    if profile.cost_policy != "free_verified":
        return None
    evidence = profile.evidence or {}
    now = (clock or time.time)()
    snapshot = evidence.get("snapshot")
    if snapshot:
        reader = snapshot_reader
        if reader is None:
            path = pathlib.Path(snapshot)
            if not path.exists():
                raise ProfileError("evidence snapshot not found: %s" % snapshot)
            reader = lambda value: path.read_text(encoding="utf-8")  # noqa: E731
        try:
            data = json.loads(reader(snapshot))
        except (OSError, ValueError):
            raise ProfileError("evidence snapshot is not valid JSON") from None
        if not isinstance(data, dict):
            raise ProfileError("evidence snapshot must be a JSON object")
        if str(data.get("model", "")).strip().lower() != profile.model.strip().lower():
            raise ProfileError("evidence snapshot is for another model")
        expires_at = data.get("evidence_expires_at")
        if type(expires_at) not in (int, float) or isinstance(expires_at, bool):
            raise ProfileError("evidence snapshot has no numeric evidence_expires_at")
        expires_at = float(expires_at)
    else:
        expires_at = float(evidence["expires_at"])
    if expires_at <= now:
        raise ProfileError("free-tier evidence has expired; refresh it")
    return expires_at


def profile_from_form(*, profile_id=None, form):
    """Build a validated Profile from desktop-form values (no secret handling)."""
    if not isinstance(form, dict):
        raise ProfileError("form must be a dict")
    policy = str(form.get("cost_policy") or "free_verified").strip()
    capabilities = form.get("capabilities") or ["text"]
    if not isinstance(capabilities, (list, tuple, set, frozenset)):
        raise ProfileError("capabilities must be a list")
    evidence = None
    if policy == "free_verified":
        snapshot = str(form.get("evidence_snapshot") or "").strip()
        raw_expires = form.get("evidence_expires_at")
        if snapshot:
            evidence = {"snapshot": snapshot}
        elif raw_expires not in (None, ""):
            try:
                evidence = {"expires_at": float(raw_expires)}
            except (TypeError, ValueError):
                raise ProfileError("evidence_expires_at must be a unix timestamp") from None
    return validate_profile(
        Profile(
            id=str(profile_id or new_profile_id(form.get("name"))),
            name=str(form.get("name") or ""),
            base_url=str(form.get("base_url") or ""),
            model=str(form.get("model") or ""),
            cost_policy=policy,
            capabilities=frozenset(str(item) for item in capabilities),
            evidence=evidence,
            enabled=bool(form.get("enabled", True)),
        )
    )