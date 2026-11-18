"""Provider API keys in the Windows Credential Manager.

The design decision is the native Win32 generic credential store under the fixed
namespace tokensfree/<provider>/<alias> (see docs/credential-boundary.md). No
files, environment variables or logs ever contain the secret.

Only Windows is implemented; every other platform fails closed instead of
silently falling back to plaintext. The OS store protects the secret at rest for
the current user, but it is not per-process isolation: any process running as
the same user can read it.
"""
import ctypes
import re
import sys

NAMESPACE = "tokensfree"
DEFAULT_ALIAS = "env-key"
CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168
MAX_BLOB_BYTES = 5 * 512  # CRED_MAX_CREDENTIAL_BLOB_SIZE
PROVIDER_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


class CredentialError(Exception):
    """Raised for unsupported platforms, missing entries and OS API failures."""


def is_supported():
    return sys.platform == "win32"


_advapi32 = None
_CREDENTIALW = None
_PCREDENTIALW = None

if sys.platform == "win32":
    from ctypes import wintypes as _wt

    class _FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", _wt.DWORD), ("dwHighDateTime", _wt.DWORD)]

    class _CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", _wt.DWORD),
            ("Type", _wt.DWORD),
            ("TargetName", _wt.LPWSTR),
            ("Comment", _wt.LPWSTR),
            ("LastWritten", _FILETIME),
            ("CredentialBlobSize", _wt.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
            ("Persist", _wt.DWORD),
            ("AttributeCount", _wt.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", _wt.LPWSTR),
            ("UserName", _wt.LPWSTR),
        ]

    _PCREDENTIALW = ctypes.POINTER(_CREDENTIALW)
    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _advapi32.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), _wt.DWORD]
    _advapi32.CredWriteW.restype = _wt.BOOL
    _advapi32.CredReadW.argtypes = [_wt.LPCWSTR, _wt.DWORD, _wt.DWORD, ctypes.POINTER(_PCREDENTIALW)]
    _advapi32.CredReadW.restype = _wt.BOOL
    _advapi32.CredDeleteW.argtypes = [_wt.LPCWSTR, _wt.DWORD, _wt.DWORD]
    _advapi32.CredDeleteW.restype = _wt.BOOL
    _advapi32.CredFree.argtypes = [ctypes.c_void_p]
    _advapi32.CredFree.restype = None


def _require_supported():
    if not is_supported():
        raise CredentialError(
            "system credential store is only implemented for Windows Credential Manager"
        )


def target_name(provider, alias=DEFAULT_ALIAS):
    if not isinstance(provider, str) or not PROVIDER_RE.match(provider):
        raise ValueError("provider must match [a-z0-9][a-z0-9-]{0,31}")
    if not isinstance(alias, str) or not ALIAS_RE.match(alias):
        raise ValueError("alias must match [a-z0-9][a-z0-9-]{0,31}")
    return NAMESPACE + "/" + provider + "/" + alias


def _wipe(buffer, size):
    try:
        ctypes.memset(ctypes.addressof(buffer), 0, size)
    except (ValueError, TypeError):
        pass


def save_secret(provider, secret, *, alias=DEFAULT_ALIAS, comment="tokensfree provider API key"):
    _require_supported()
    target = target_name(provider, alias)
    if not isinstance(secret, str) or not secret:
        raise ValueError("secret must be a non-empty string")
    data = secret.encode("utf-8")
    if len(data) > MAX_BLOB_BYTES:
        raise ValueError("secret is too long for the system credential store")
    blob = (ctypes.c_byte * len(data)).from_buffer_copy(data)
    credential = _CREDENTIALW()
    credential.Flags = 0
    credential.Type = CRED_TYPE_GENERIC
    credential.TargetName = target
    credential.Comment = comment
    credential.CredentialBlobSize = len(data)
    credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_byte))
    credential.Persist = CRED_PERSIST_LOCAL_MACHINE
    credential.AttributeCount = 0
    credential.Attributes = None
    credential.TargetAlias = None
    credential.UserName = None
    try:
        if not _advapi32.CredWriteW(ctypes.byref(credential), 0):
            raise CredentialError("CredWriteW failed with error %d" % ctypes.get_last_error())
    finally:
        _wipe(blob, len(data))
    return target


def load_secret(provider, *, alias=DEFAULT_ALIAS):
    _require_supported()
    target = target_name(provider, alias)
    credential = _PCREDENTIALW()
    if not _advapi32.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(credential)):
        error = ctypes.get_last_error()
        if error == ERROR_NOT_FOUND:
            raise CredentialError("no stored credential for provider %r" % provider)
        raise CredentialError("CredReadW failed with error %d" % error)
    try:
        size = int(credential.contents.CredentialBlobSize)
        raw = ctypes.string_at(credential.contents.CredentialBlob, size) if size else b""
    finally:
        _advapi32.CredFree(credential)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise CredentialError("stored credential is not valid UTF-8") from None


def delete_secret(provider, *, alias=DEFAULT_ALIAS):
    _require_supported()
    target = target_name(provider, alias)
    if not _advapi32.CredDeleteW(target, CRED_TYPE_GENERIC, 0):
        error = ctypes.get_last_error()
        if error == ERROR_NOT_FOUND:
            return False
        raise CredentialError("CredDeleteW failed with error %d" % error)
    return True
