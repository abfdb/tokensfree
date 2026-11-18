"""The single admission gate.

Every adapter attempt must present an Admission issued here. The gate applies the
existing fail-closed policy (providers.evaluate) and then a second configuration check,
so a mistake in either layer still cannot produce a real call.
"""
from providers import evaluate
from tokensfree.adapters.base import Admission, _ADMISSION_TOKEN


class AdmissionDenied(Exception):
    """Stable machine-readable refusal reason."""

    __slots__ = ("reason",)

    def __init__(self, reason):
        self.reason = str(reason)
        super().__init__(self.reason)


def admit(spec, config, authorized, now, required=frozenset({"text"})):
    if not isinstance(authorized, (set, frozenset)):
        raise TypeError("authorized must be a set")
    decision = evaluate(spec, authorized, now, required, allow_real=config.enable_real)
    if not decision.allowed:
        raise AdmissionDenied(decision.reason)
    if spec.simulation is False and config.enable_real is False:
        raise AdmissionDenied("real_calls_disabled")
    return Admission(
        provider=spec.name,
        capabilities=spec.capabilities,
        simulation=spec.simulation,
        _token=_ADMISSION_TOKEN,
    )
