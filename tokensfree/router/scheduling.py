"""Account scheduling: pick a usable account for one provider.

Pure and deterministic. It never mutates accounts; the caller records usage
(AccountsRepo.set_status / LedgerRepo) after the attempt.

Rules:
- only accounts in a usable status;
- cooldown must have expired;
- round-robin by least-recently-used, never-used first;
- per-account concurrency cap, so one slow request cannot starve an account.
"""
from dataclasses import dataclass

USABLE_STATUSES = frozenset({"active"})


@dataclass(frozen=True)
class AccountView:
    id: int
    provider: str
    status: str
    cooldown_until: float | None = None
    last_used_at: float | None = None


def is_usable(account, now):
    if account.status not in USABLE_STATUSES:
        return False
    if account.cooldown_until is not None and account.cooldown_until > now:
        return False
    return True


def select_account(accounts, *, provider, now, exclude_ids=frozenset(), in_flight=None, max_concurrency=1):
    if not isinstance(provider, str) or not provider:
        raise ValueError("provider must be a non-empty string")
    if type(max_concurrency) is not int or max_concurrency < 1:
        raise ValueError("max_concurrency must be a positive int")
    busy = in_flight or {}
    candidates = []
    for account in accounts:
        if not isinstance(account, AccountView):
            raise TypeError("accounts must contain AccountView instances")
        if account.provider != provider or account.id in exclude_ids or not is_usable(account, now):
            continue
        if busy.get(account.id, 0) >= max_concurrency:
            continue
        candidates.append(account)
    if not candidates:
        return None
    return min(candidates, key=lambda account: (account.last_used_at is not None, account.last_used_at or 0.0, account.id))
