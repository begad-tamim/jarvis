import pytest

from jarvis.data.synthetic import SyntheticMarket
from jarvis.models import Account, Signal


@pytest.fixture
def market():
    return SyntheticMarket(seed=3)


@pytest.fixture
def account():
    return Account(equity=100_000, cash=100_000, buying_power=100_000)


def make_signal(symbol="NVDA", score=70.0, price=100.0, atr=2.0, action="buy", dollar_volume=5e7):
    return Signal(symbol, score, price, atr, 60.0, action, {}, "test", dollar_volume)
