"""Candidate selection.

Pure function: filters by authorization and capability, preserves configuration order.
It deliberately does not decide free status, health or cooldowns - that is admission's job.
"""
from dataclasses import replace

from providers import ProviderSpec
from tokensfree.adapters.base import ChatRequest


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


AUTO_MODEL = "tokensfree-auto"


def specs_for_model(specs, requested):
    """Resolve the client's model id to the providers that may serve it.

    AUTO_MODEL means "let the router choose"; any other id must exactly match a
    configured provider model. An empty result means model_not_found.
    """
    if not isinstance(requested, str) or not requested:
        raise ValueError("requested must be a non-empty string")
    if requested == AUTO_MODEL:
        return tuple(specs)
    return tuple(spec for spec in specs if spec.model == requested)


def bind_auto_model(request, spec):
    """Bind the internal selector to the admitted candidate for this attempt."""
    if isinstance(request, ChatRequest) and request.model == AUTO_MODEL:
        return replace(request, model=spec.model)
    return request
