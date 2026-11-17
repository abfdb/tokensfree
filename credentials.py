"""Credential boundary contracts. Production resolution is deliberately disabled.

No environment, filesystem, OS vault, keyring autodiscovery or network access.
The sole working backend contains a fixed synthetic fixture, never user secrets.
"""
from dataclasses import dataclass, field
import math
import re

class CredentialUnavailable(RuntimeError):
    """Safe fixed error; backend diagnostics must not propagate."""

@dataclass(frozen=True)
class Binding:
    alias: str
    provider: str
    origin: str
    model: str
    expires_at: float

    def __post_init__(self):
        if not isinstance(self.alias, str) or not re.fullmatch(r'[a-z0-9-]{1,40}', self.alias):
            raise ValueError('invalid credential alias')
        if type(self.provider) is not str or type(self.origin) is not str or self.provider != 'google-gemini-developer' or self.origin != 'https://generativelanguage.googleapis.com':
            raise ValueError('unsupported credential destination')
        if not isinstance(self.model, str) or not re.fullmatch(r'gemini-[a-z0-9.-]{1,80}', self.model):
            raise ValueError('invalid credential model')
        if not _finite(self.expires_at): raise ValueError('invalid credential expiry')


def _finite(value):
    try: return type(value) in (int, float) and math.isfinite(value)
    except OverflowError: return False


class DisabledSystemVault:
    """Fail closed until an approved OS backend is implemented and tested."""
    def read(self, alias):
        raise CredentialUnavailable('system_vault_not_implemented')


class SyntheticVault:
    """Offline test only. No insertion method; cannot collect user credentials."""
    def read(self, alias):
        if alias != 'fixture': raise CredentialUnavailable('credential_unavailable')
        return 'tokensfree-synthetic-credential-not-a-key'


@dataclass(repr=False)
class _Lease:
    # A redacted repr is log hygiene, not a Python process isolation guarantee.
    value: str = field(repr=False)
    def __repr__(self): return '<CredentialLease redacted>'
    def __str__(self): return '<CredentialLease redacted>'


class CredentialBroker:
    """Trusted backend component, never exposed through HTTP or browser config.

    Bindings are fixed at construction by server configuration. Caller cannot
    choose a vault path, raw secret, arbitrary host or broaden model scope.
    This release issues synthetic leases only; native mode always fails.
    """
    def __init__(self, bindings=(), *, fixture=False):
        if type(fixture) is not bool: raise ValueError('invalid fixture mode')
        items = tuple(bindings)
        if any(type(item) is not Binding for item in items): raise ValueError('invalid binding')
        if len({item.alias for item in items}) != len(items): raise ValueError('duplicate alias')
        self._bindings = {item.alias:item for item in items}
        self._vault = SyntheticVault() if fixture else DisabledSystemVault()
        self._fixture = fixture

    def resolve_fixture(self, alias, provider, origin, model, *, now):
        if not self._fixture: raise CredentialUnavailable('real_credentials_disabled')
        if not isinstance(alias, str): raise CredentialUnavailable('credential_scope_denied')
        binding = self._bindings.get(alias)
        if binding is None or (provider, origin, model) != (binding.provider, binding.origin, binding.model):
            raise CredentialUnavailable('credential_scope_denied')
        if not _finite(now) or now >= binding.expires_at:
            raise CredentialUnavailable('credential_expired')
        try:
            value = self._vault.read(alias)
        except Exception:
            raise CredentialUnavailable('credential_unavailable') from None
        if type(value) is not str or value != 'tokensfree-synthetic-credential-not-a-key':
            raise CredentialUnavailable('non_fixture_credential_rejected')
        return _Lease(value)
