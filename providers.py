"""Provider contracts and fail-closed policy. No HTTP transport or credentials.

Eligibility is a trusted server-side assertion, never a client billing claim.
Real providers remain disabled until separate integration and account verification.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Protocol
import math

class Outcome(str, Enum):
    SUCCESS = 'success'
    EXHAUSTED = 'exhausted'
    LIMITED = 'limited'
    AUTH = 'auth'
    REFUSAL = 'refusal'
    UNAVAILABLE = 'unavailable'

@dataclass(frozen=True)
class ProviderSpec:
    name: str
    model: str
    cost_evidence: str = 'unknown'
    evidence_expires_at: float = 0
    capabilities: frozenset = frozenset({'text'})
    enabled: bool = True
    simulation: bool = True

@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str

class Adapter(Protocol):
    def complete(self, messages: list) -> Outcome: ...

class MockAdapter:
    """Deterministic local fixture. No prompts retained, no network operations."""
    def __init__(self, outcome: str): self.outcome = Outcome(outcome)
    def complete(self, messages: list) -> Outcome: return self.outcome


def evaluate(spec: ProviderSpec, authorized: frozenset, now: float,
             required: frozenset = frozenset({'text'}), *, allow_real: bool = False) -> Decision:
    """Fail-closed policy. Real providers need allow_real plus explicit free evidence.

    The default (allow_real=False) keeps the legacy guarantee: a real provider can
    never reach an adapter, whatever anyone believes about its billing.
    """
    if not math.isfinite(now): return Decision(False, 'invalid_clock')
    if not spec.enabled: return Decision(False, 'disabled')
    if spec.name not in authorized: return Decision(False, 'not_authorized')
    if not required.issubset(spec.capabilities): return Decision(False, 'capability_mismatch')
    if not spec.simulation:
        # Real providers: the switch alone is never enough; evidence must be explicit
        # and unexpired, and authorization already passed above.
        if not allow_real: return Decision(False, 'real_provider_not_implemented')
        if spec.cost_evidence != 'free_verified': return Decision(False, 'unverified_free_status')
        if not math.isfinite(spec.evidence_expires_at) or spec.evidence_expires_at <= now:
            return Decision(False, 'expired_free_evidence')
        return Decision(True, 'verified_real')
    if spec.cost_evidence != 'mock_zero_cost': return Decision(False, 'unverified_free_status')
    if not math.isfinite(spec.evidence_expires_at) or spec.evidence_expires_at <= now:
        return Decision(False, 'expired_free_evidence')
    return Decision(True, 'verified_mock_only')
