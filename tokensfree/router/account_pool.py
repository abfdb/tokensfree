"""Account pool: turn stored accounts into a usable per-provider picker.

It composes the pure scheduler with the SQLite repositories:
- selection is delegated to router.scheduling.select_account;
- a pick touches last_used_at and, when a request id is given, writes one
  ledger row so usage stays auditable;
- only a platform signal may exhaust an account.
"""
import time

from tokensfree.router.scheduling import AccountView, select_account


class AccountPool:
    def __init__(self, accounts_repo, ledger_repo=None, *, max_concurrency=1, clock=time.time):
        if type(max_concurrency) is not int or max_concurrency < 1:
            raise ValueError("max_concurrency must be a positive int")
        self.accounts = accounts_repo
        self.ledger = ledger_repo
        self.max_concurrency = max_concurrency
        self.clock = clock

    def _views(self, provider):
        return [
            AccountView(
                id=row["id"],
                provider=row["provider"],
                status=row["status"],
                cooldown_until=row["cooldown_until"],
                last_used_at=row["last_used_at"],
            )
            for row in self.accounts.list_active(provider)
        ]

    def pick(self, provider, *, now=None, request_id=None, exclude_ids=frozenset(), in_flight=None, max_concurrency=None):
        now = self.clock() if now is None else now
        cap = self.max_concurrency if max_concurrency is None else max_concurrency
        view = select_account(
            self._views(provider),
            provider=provider,
            now=now,
            exclude_ids=exclude_ids,
            in_flight=in_flight,
            max_concurrency=cap,
        )
        if view is None:
            return None
        self.accounts.touch(view.id, now=now)
        if self.ledger is not None and request_id is not None:
            self.ledger.append(
                view.id, view.provider, request_id, "request_attempt", -1.0, "request", "estimated", now=now
            )
        return view

    def cool_down(self, account_id, *, seconds, now=None):
        now = self.clock() if now is None else now
        if type(seconds) not in (int, float) or seconds <= 0:
            raise ValueError("seconds must be a positive number")
        self.accounts.set_cooldown(account_id, float(now) + float(seconds))

    def exhaust(self, account_id, *, provider, request_id, detail=None, now=None):
        now = self.clock() if now is None else now
        if self.ledger is not None:
            self.ledger.platform_signal(account_id, provider=provider, request_id=request_id, detail=detail, now=now)
        else:
            self.accounts.set_status(account_id, "exhausted")
        self.accounts.clear_cooldown(account_id)

    def invalidate(self, account_id):
        self.accounts.set_status(account_id, "invalid")
        self.accounts.clear_cooldown(account_id)

    def reactivate(self, account_id):
        self.accounts.clear_cooldown(account_id)
        self.accounts.set_status(account_id, "active")
