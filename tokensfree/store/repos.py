"""Repositories over the SQLite schema.

Design rules encoded here:
- idempotency keys move reserved -> done; replay only returns a stored final response;
- the quota ledger is append-only and only a platform_signal can mark an account exhausted;
- events store no prompt text, only whitelisted metadata.
"""
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Reserve:
    status: str
    response_json: str | None = None


class IdempotencyRepo:
    def __init__(self, connection, *, capacity=100):
        if type(capacity) is not int or capacity < 1:
            raise ValueError("capacity must be a positive int")
        self._conn = connection
        self.capacity = capacity

    def purge_expired(self, now=None):
        now = time.time() if now is None else now
        cursor = self._conn.execute("DELETE FROM idempotency_keys WHERE expires_at <= ?", (now,))
        self._conn.commit()
        return cursor.rowcount

    def reserve(self, key, request_hash, *, now, ttl_s=300.0):
        if not isinstance(key, str) or not key:
            raise ValueError("invalid idempotency key")
        if not isinstance(request_hash, str) or not request_hash:
            raise ValueError("invalid request hash")
        self.purge_expired(now)
        row = self._conn.execute(
            "SELECT request_hash, status, response_json FROM idempotency_keys WHERE key = ?", (key,)
        ).fetchone()
        if row is not None:
            if row["request_hash"] != request_hash:
                return Reserve("conflict")
            if row["status"] == "done":
                return Reserve("replay", row["response_json"])
            return Reserve("in_flight")
        count = self._conn.execute("SELECT COUNT(*) FROM idempotency_keys").fetchone()[0]
        if count >= self.capacity:
            return Reserve("capacity")
        self._conn.execute(
            "INSERT INTO idempotency_keys(key, request_hash, status, response_json, created_at, expires_at)"
            " VALUES (?, ?, 'reserved', NULL, ?, ?)",
            (key, request_hash, now, now + ttl_s),
        )
        self._conn.commit()
        return Reserve("new")

    def complete(self, key, response_json):
        cursor = self._conn.execute(
            "UPDATE idempotency_keys SET status = 'done', response_json = ? WHERE key = ? AND status = 'reserved'",
            (response_json, key),
        )
        self._conn.commit()
        if cursor.rowcount != 1:
            raise KeyError(key)

    def cancel(self, key):
        cursor = self._conn.execute(
            "DELETE FROM idempotency_keys WHERE key = ? AND status = 'reserved'", (key,)
        )
        self._conn.commit()
        return cursor.rowcount


class AccountsRepo:
    def __init__(self, connection):
        self._conn = connection

    def add(self, provider, alias, credential_ref, fingerprint, *, now, tier="real_declared", status="pending"):
        cursor = self._conn.execute(
            "INSERT INTO accounts(provider, alias, credential_ref, fingerprint, tier, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (provider, alias, credential_ref, fingerprint, tier, status, now),
        )
        self._conn.commit()
        return cursor.lastrowid

    def ensure(self, provider, alias, credential_ref, fingerprint, *, now, tier="real_declared", status="active"):
        """Create the account once; return its id on later starts (no duplicates)."""
        self._conn.execute(
            "INSERT OR IGNORE INTO accounts(provider, alias, credential_ref, fingerprint, tier, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (provider, alias, credential_ref, fingerprint, tier, status, now),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT id FROM accounts WHERE provider = ? AND alias = ?", (provider, alias)
        ).fetchone()
        return row["id"]

    def refresh_credentials(self, account_id, fingerprint, *, status="active", now=None):
        """A replaced key gets a fresh fingerprint and is reactivated (cooldown cleared)."""
        self._conn.execute(
            "UPDATE accounts SET fingerprint = ?, status = ?, cooldown_until = NULL WHERE id = ?",
            (fingerprint, status, account_id),
        )
        self._conn.commit()
        return self.get(account_id)

    def get(self, account_id):
        return self._conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()

    def touch(self, account_id, *, now):
        self._conn.execute("UPDATE accounts SET last_used_at = ? WHERE id = ?", (now, account_id))
        self._conn.commit()

    def set_cooldown(self, account_id, until):
        self._conn.execute("UPDATE accounts SET cooldown_until = ? WHERE id = ?", (until, account_id))
        self._conn.commit()

    def clear_cooldown(self, account_id):
        self._conn.execute("UPDATE accounts SET cooldown_until = NULL WHERE id = ?", (account_id,))
        self._conn.commit()

    def set_status(self, account_id, status, *, now=None):
        self._conn.execute("UPDATE accounts SET status = ? WHERE id = ?", (status, account_id))
        if now is not None:
            self._conn.execute("UPDATE accounts SET last_used_at = ? WHERE id = ?", (now, account_id))
        self._conn.commit()

    def list_active(self, provider=None):
        if provider is None:
            rows = self._conn.execute("SELECT * FROM accounts WHERE status = 'active' ORDER BY id")
        else:
            rows = self._conn.execute(
                "SELECT * FROM accounts WHERE status = 'active' AND provider = ? ORDER BY id", (provider,)
            )
        return tuple(rows)


class LedgerRepo:
    def __init__(self, connection):
        self._conn = connection

    def append(self, account_id, provider, request_id, kind, delta_units, unit, accuracy, detail=None, *, now=None):
        cursor = self._conn.execute(
            "INSERT INTO quota_ledger(account_id, provider, request_id, kind, delta_units, unit, accuracy, detail, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                account_id,
                provider,
                request_id,
                kind,
                float(delta_units),
                unit,
                accuracy,
                detail,
                time.time() if now is None else now,
            ),
        )
        self._conn.commit()
        return cursor.lastrowid

    def balance(self, account_id, *, unit="request"):
        row = self._conn.execute(
            "SELECT COALESCE(SUM(delta_units), 0.0) FROM quota_ledger WHERE account_id = ? AND unit = ?",
            (account_id, unit),
        ).fetchone()
        return float(row[0])

    def platform_signal(self, account_id, *, provider, request_id, detail=None, now=None):
        self.append(
            account_id, provider, request_id, "platform_signal", 0.0, "request", "exact", detail=detail, now=now
        )
        self._conn.execute("UPDATE accounts SET status = 'exhausted' WHERE id = ?", (account_id,))
        self._conn.commit()


class EventsRepo:
    def __init__(self, connection):
        self._conn = connection

    def append(
        self,
        request_id,
        *,
        outcome=None,
        provider=None,
        account_id=None,
        latency_ms=None,
        bytes_in=None,
        bytes_out=None,
        detail=None,
        level="info",
        at=None,
    ):
        self._conn.execute(
            "INSERT INTO events(request_id, at, level, provider, account_id, outcome, latency_ms, bytes_in, bytes_out, detail)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                request_id,
                time.time() if at is None else at,
                level,
                provider,
                account_id,
                outcome,
                latency_ms,
                bytes_in,
                bytes_out,
                detail,
            ),
        )
        self._conn.commit()

    def recent(self, limit=50):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("invalid limit")
        return tuple(self._conn.execute("SELECT * FROM events ORDER BY at DESC, id DESC LIMIT ?", (limit,)))

    def purge_older_than(self, *, days=7, now=None):
        cutoff = (time.time() if now is None else now) - days * 86400
        cursor = self._conn.execute("DELETE FROM events WHERE at < ?", (cutoff,))
        self._conn.commit()
        return cursor.rowcount


class ProviderHealthRepo:
    """Circuit-breaker snapshots so an open circuit survives a restart."""

    def __init__(self, connection):
        self._conn = connection

    def get(self, provider):
        row = self._conn.execute("SELECT * FROM provider_health WHERE provider = ?", (provider,)).fetchone()
        if row is None:
            return None
        return {
            "state": row["circuit_state"],
            "fail_streak": row["fail_streak"],
            "window_fails": row["window_fails"],
            "open_until": row["open_until"],
        }

    def save(self, provider, snapshot):
        if not isinstance(snapshot, dict):
            raise TypeError("snapshot must be a dict")
        self._conn.execute(
            "INSERT INTO provider_health(provider, fail_streak, window_start, window_fails, circuit_state, open_until)"
            " VALUES (?, ?, NULL, ?, ?, ?)"
            " ON CONFLICT(provider) DO UPDATE SET fail_streak = excluded.fail_streak,"
            " window_fails = excluded.window_fails, circuit_state = excluded.circuit_state,"
            " open_until = excluded.open_until",
            (
                provider,
                int(snapshot.get("fail_streak", 0)),
                int(snapshot.get("window_fails", 0)),
                str(snapshot.get("state", "closed")),
                snapshot.get("open_until"),
            ),
        )
        self._conn.commit()

    def all(self):
        return {row["provider"]: self.get(row["provider"]) for row in self._conn.execute("SELECT provider FROM provider_health")}
