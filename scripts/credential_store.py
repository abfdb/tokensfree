"""Read/write the provider API key in the Windows Credential Manager.

  python scripts/credential_store.py set --provider zhipu            # prompts (hidden input)
  python scripts/credential_store.py set --provider zhipu --stdin    # automation: key on stdin
  python scripts/credential_store.py status --provider zhipu
  python scripts/credential_store.py clear --provider zhipu

The secret is never printed and never touches a file. Only Windows is supported;
every other platform fails closed.
Exit codes: 0 ok, 2 unsupported platform or input error.
"""
import argparse
import getpass
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tokensfree import credstore  # noqa: E402


def emit(payload, code=0):
    print(json.dumps(payload, ensure_ascii=False))
    return code


def main():
    parser = argparse.ArgumentParser(description="Manage the provider API key in the Windows Credential Manager")
    parser.add_argument("command", choices=["set", "status", "clear"])
    parser.add_argument("--provider", required=True)
    parser.add_argument("--alias", default=credstore.DEFAULT_ALIAS)
    parser.add_argument("--stdin", action="store_true", help="read the key from stdin instead of prompting")
    args = parser.parse_args()

    if not credstore.is_supported():
        return emit({"verified": False, "reason": "unsupported_platform"}, 2)

    try:
        if args.command == "set":
            if args.stdin:
                secret = sys.stdin.readline().strip()
            else:
                secret = getpass.getpass("API key for %s: " % args.provider).strip()
            if not secret:
                return emit({"verified": False, "reason": "empty_secret"}, 2)
            target = credstore.save_secret(args.provider, secret, alias=args.alias)
            return emit({"verified": True, "stored": target})
        if args.command == "status":
            try:
                credstore.load_secret(args.provider, alias=args.alias)
                present = True
            except credstore.CredentialError:
                present = False
            return emit({"verified": True, "provider": args.provider, "alias": args.alias, "present": present})
        deleted = credstore.delete_secret(args.provider, alias=args.alias)
        return emit({"verified": True, "deleted": deleted})
    except (credstore.CredentialError, ValueError) as error:
        return emit({"verified": False, "reason": type(error).__name__, "detail": str(error)}, 2)


if __name__ == "__main__":
    raise SystemExit(main())
