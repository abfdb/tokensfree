"""Models catalog: the single source of truth for /v1/models and static client config."""
from providers import ProviderSpec


def models_payload(specs):
    data = []
    for spec in specs:
        if not isinstance(spec, ProviderSpec):
            raise TypeError("specs must contain ProviderSpec instances")
        data.append(
            {
                "id": spec.model,
                "object": "model",
                "owned_by": spec.name,
                "capabilities": sorted(spec.capabilities),
                "simulation": spec.simulation,
            }
        )
    return {"object": "list", "data": data}
