"""Static route table shared by every HTTP backend.

The table is data, not code: resolving a request never performs policy work.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    endpoint: str


ROUTES = (
    Route("GET", "/v1/models", "models"),
    Route("POST", "/v1/chat/completions", "chat"),
    Route("GET", "/api/status", "status"),
)


def resolve(method, path):
    if not isinstance(method, str):
        raise TypeError("method must be a str")
    if not isinstance(path, str):
        raise TypeError("path must be a str")
    upper = method.upper()
    for route in ROUTES:
        if route.method == upper and route.path == path:
            return route
    return None


def allowed_methods(path):
    if not isinstance(path, str):
        raise TypeError("path must be a str")
    return tuple(route.method for route in ROUTES if route.path == path)
