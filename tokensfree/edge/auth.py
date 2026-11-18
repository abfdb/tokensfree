"""Edge authentication and origin checks as pure functions.

Ported from the legacy server's behaviour so every backend applies the same rules:
loopback Host only, same-origin only, constant-time local token comparison.
"""
import hmac


def allowed_hosts(port):
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("invalid port")
    return frozenset({"127.0.0.1:" + str(port), "localhost:" + str(port)})


def origin_allowed(host_header, origin_header, port):
    hosts = allowed_hosts(port)
    if host_header not in hosts:
        return False
    if origin_header is None:
        return True
    return origin_header in {"http://" + host for host in hosts}


def token_matches(expected, provided):
    if not isinstance(expected, str) or not expected:
        return False
    if not isinstance(provided, str) or not provided:
        return False
    return hmac.compare_digest(expected.encode("utf-8"), provided.encode("utf-8"))


def bearer_token(header_value):
    if not isinstance(header_value, str):
        return None
    parts = header_value.strip().split(maxsplit=1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None
