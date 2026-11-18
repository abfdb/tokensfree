"""Account scheduling: round-robin over usable accounts, pure and deterministic."""
import pytest

from tokensfree.router.scheduling import AccountView, select_account


def account(account_id, *, provider="zhipu", status="active", cooldown_until=None, last_used_at=None):
    return AccountView(id=account_id, provider=provider, status=status, cooldown_until=cooldown_until, last_used_at=last_used_at)


def test_picks_an_active_account():
    chosen = select_account([account(1)], provider="zhipu", now=100)
    assert chosen is not None and chosen.id == 1


def test_skips_unusable_statuses():
    accounts = [
        account(1, status="pending"),
        account(2, status="suspended"),
        account(3, status="exhausted"),
        account(4, status="invalid"),
        account(5),
    ]
    assert select_account(accounts, provider="zhipu", now=100).id == 5


def test_skips_accounts_in_cooldown_until_it_expires():
    accounts = [account(1, cooldown_until=150), account(2)]
    assert select_account(accounts, provider="zhipu", now=100).id == 2
    assert select_account(accounts, provider="zhipu", now=151).id == 1


def test_round_robin_prefers_least_recently_used():
    accounts = [account(1, last_used_at=10), account(2, last_used_at=20), account(3, last_used_at=5)]
    assert select_account(accounts, provider="zhipu", now=100).id == 3
    assert select_account(accounts, provider="zhipu", now=100, exclude_ids={3}).id == 1


def test_never_used_beats_used_accounts():
    accounts = [account(1, last_used_at=10), account(2)]
    assert select_account(accounts, provider="zhipu", now=100).id == 2


def test_provider_filter_is_strict():
    accounts = [account(1, provider="zhipu"), account(2, provider="openrouter")]
    assert select_account(accounts, provider="openrouter", now=100).id == 2
    assert select_account(accounts, provider="gemini", now=100) is None


def test_concurrency_cap_excludes_saturated_accounts():
    accounts = [account(1), account(2)]
    in_flight = {1: 1}
    assert select_account(accounts, provider="zhipu", now=100, in_flight=in_flight, max_concurrency=1).id == 2
    in_flight = {1: 1, 2: 1}
    assert select_account(accounts, provider="zhipu", now=100, in_flight=in_flight, max_concurrency=1) is None


def test_returns_none_when_nothing_is_usable():
    assert select_account([], provider="zhipu", now=100) is None


def test_invalid_arguments_are_rejected():
    with pytest.raises(TypeError):
        select_account([{"id": 1}], provider="zhipu", now=100)
    with pytest.raises(ValueError):
        select_account([account(1)], provider="", now=100)
    with pytest.raises(ValueError):
        select_account([account(1)], provider="zhipu", now=100, max_concurrency=0)
