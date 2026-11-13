import pytest

from callgate.ratelimit import TokenBucket


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_burst_then_refill():
    c = Clock()
    b = TokenBucket(rate_per_s=1, burst=2, clock=c)
    assert b.try_take() and b.try_take()
    assert not b.try_take()
    assert b.wait_time() == pytest.approx(1.0)
    c.t = 1.0
    assert b.try_take()
    c.t = 100
    assert b.try_take() and b.try_take() and not b.try_take()


def test_invalid():
    with pytest.raises(ValueError):
        TokenBucket(0, 1)
