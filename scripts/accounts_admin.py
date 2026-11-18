"""Inspect and repair account state in the SQLite store.

  python scripts/accounts_admin.py --db state.db                 # list
  python scripts/accounts_admin.py --db state.db activate zhipu # reactivate one provider
  python scripts/accounts_admin.py --db state.db reset-cooldown # clear all cooldowns

Exit codes: 0 ok, 2 cannot open db, 3 provider not found.
"""
import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tokensfree.store.db import connect, migrate  # noqa: E402
from tokensfree.store.repos import AccountsRepo, LedgerRepo  # noqa: E402


def load(db_path):
    path = pathlib.Path(db_path)
    if not path.exists():
        print(json.dumps({"error": "db_not_found", "path": str(path)}))
        raise SystemExit(2)
    connection = connect(str(path))
    migrate(connection)
    return connection


def rows(connection):
    ledger = LedgerRepo(connection)
    data = []
    for row in connection.execute("SELECT * FROM accounts ORDER BY id"):
        data.append(
            {
                "id": row["id"],
                "provider": row["provider"],
                "status": row["status"],
                "cooldown_until": row["cooldown_until"],
                "fingerprint": (row["fingerprint"] or "")[:12],
                "requests_charged": int(round(-ledger.balance(row["id"], unit="request"))),
            }
        )
    return data


def main():
    parser = argparse.ArgumentParser(description="Inspect and repair tokensfree account state")
    parser.add_argument("--db", required=True, help="path to the SQLite file used by the host")
    parser.add_argument("command", nargs="?", default="list", choices=["list", "activate", "reset-cooldown"])
    parser.add_argument("provider", nargs="?", default=None)
    args = parser.parse_args()

    connection = load(args.db)
    try:
        accounts = AccountsRepo(connection)
        if args.command == "list":
            print(json.dumps(rows(connection), ensure_ascii=False, indent=2))
            return 0
        if args.command == "activate":
            if not args.provider:
                print(json.dumps({"error": "provider_required"}))
                return 2
            matches = [row for row in connection.execute("SELECT id FROM accounts WHERE provider = ?", (args.provider,))]
            if not matches:
                print(json.dumps({"error": "provider_not_found", "provider": args.provider}))
                return 3
            for row in matches:
                accounts.set_status(row["id"], "active")
                accounts.clear_cooldown(row["id"])
            print(json.dumps({"activated": args.provider, "rows": len(matches)}))
            return 0
        if args.command == "reset-cooldown":
            changed = connection.execute("UPDATE accounts SET cooldown_until = NULL").rowcount
            connection.commit()
            print(json.dumps({"cooldowns_cleared": changed}))
            return 0
    finally:
        connection.close()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
