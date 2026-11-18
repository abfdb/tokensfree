"""Candidate selection.

Pure function: filters by authorization and capability, preserves configuration order.
It deliberately does not decide free status, health or cooldowns - that is admission's job.
"""
from providers import ProviderSpec


def build_candidates(specs, *, allowed, required=frozenset({"text"})):
    if not isinstance(allowed, (set, frozenset)):
        raise TypeError("allowed must be a set")
    if not isinstance(required, (set, frozenset)):
        raise TypeError("required must be a set")
    selected = []
    for spec in specs:
        if not isinstance(spec, ProviderSpec):
            raise TypeError("specs must contain ProviderSpec instances")
        if spec.name not in allowed:
            continue
        if not required.issubset(spec.capabilities):
            continue
        selected.append(spec)
    return tuple(selected)
