"""Tiny KEY=VALUE env files for local startup convenience.

Only TOKENSFREE_* keys are accepted, values are used only when the real
environment does not already define the key, and errors never echo values.
This keeps a generated .env (gitignored) convenient without letting a file
silently override what the operator already exported.
"""
import pathlib
import re

KEY_RE = re.compile(r"^TOKENSFREE_[A-Z0-9_]{1,64}$")


def parse_env_file(path):
    raw = pathlib.Path(str(path)).read_text(encoding="utf-8-sig")
    values = {}
    for number, line in enumerate(raw.splitlines(), start=1):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        key, separator, value = text.partition("=")
        key = key.strip()
        if not separator:
            raise ValueError("env file line %d is not KEY=VALUE" % number)
        if not KEY_RE.match(key):
            raise ValueError("env file line %d has a non-tokensfree key" % number)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in (chr(34), chr(39)):
            value = value[1:-1]
        values[key] = value
    return values


def apply_env_file(path, environ=None):
    import os

    environ = os.environ if environ is None else environ
    values = parse_env_file(path)
    applied = []
    for key, value in values.items():
        if key in environ and str(environ[key]).strip():
            continue
        environ[key] = value
        applied.append(key)
    return applied
