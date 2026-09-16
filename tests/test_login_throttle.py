import datetime as dt

from text_evolver.db.models import LoginFailureByAccount
from text_evolver.web.login_throttle import account_retry_delay, login_is_blocked


def test_account_delay_starts_at_five_failures_and_grows_exponentially():
    assert account_retry_delay(4) == dt.timedelta(0)
    assert account_retry_delay(5) == dt.timedelta(seconds=1)
    assert account_retry_delay(6) == dt.timedelta(seconds=2)
    assert account_retry_delay(10) == dt.timedelta(seconds=32)
    assert account_retry_delay(11) == dt.timedelta(seconds=60)
    assert account_retry_delay(100) == dt.timedelta(seconds=60)


def test_account_delay_blocks_only_until_minimum_interval_has_elapsed():
    now = dt.datetime.now(dt.UTC)
    failure = LoginFailureByAccount(username="reader", failure_score=7, last_failure_at=now)

    assert login_is_blocked(None, failure, now + dt.timedelta(seconds=3))
    assert not login_is_blocked(None, failure, now + dt.timedelta(seconds=4))
