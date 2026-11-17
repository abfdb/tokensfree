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
             required: frozenset = frozenset({'text'})) -> Decision:
    if not math.isfinite(now): return Decision(False, 'invalid_clock')
    if not spec.enabled: return Decision(False, 'disabled')
    if spec.name not in authorized: return Decision(False, 'not_authorized')
    if not required.issubset(spec.capabilities): return Decision(False, 'capability_mismatch')
    # Hard block: this release cannot turn a config flag into real network access.
    if not spec.simulation: return Decision(False, 'real_provider_not_implemented')
    if spec.cost_evidence != 'mock_zero_cost': return Decision(False, 'unverified_free_status')
    if not math.isfinite(spec.evidence_expires_at) or spec.evidence_expires_at <= now:
        return Decision(False, 'expired_free_evidence')
    return Decision(True, 'verified_mock_only')
