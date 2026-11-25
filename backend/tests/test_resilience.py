"""Модульні тести політик стійкості (ЛР 7) з керованим «фальшивим» часом."""
import random

import pytest

from fitshed.gateway.resilience import (
    BreakerState,
    CircuitBreaker,
    RetryPolicy,
    TokenBucketLimiter,
    TransientError,
    run_with_retry,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def scripted(clock, script):
    """Виклик, що по черзі повертає/кидає значення зі script і «витрачає» час."""
    calls = []

    def call(timeout):
        calls.append(timeout)
        kind, cost = script[len(calls) - 1]
        clock.now += min(cost, timeout)
        if kind == "timeout" or cost > timeout:
            raise TransientError("timeout")
        if kind == "conn":
            raise TransientError("connection_error")
        return kind
    return call, calls


classify = {200: "ok", 404: "final", 500: "final", 503: "retryable"}.get
POLICY = RetryPolicy(per_try_timeout=0.5, deadline=2.0, max_attempts=3, base_delay=0.1, max_delay=1.0)


# ---------- retry ----------
def test_transient_503_is_retried_until_success():
    clock = FakeClock()
    call, calls = scripted(clock, [(503, 0.01), (503, 0.01), (200, 0.01)])
    result, attempts, reason = run_with_retry(call, classify, POLICY, retry_allowed=True,
                                              clock=clock, sleep=clock.sleep, rng=random.Random(1))
    assert (result, reason, len(calls)) == (200, "ok", 3)


@pytest.mark.parametrize("status", [404, 500])
def test_non_transient_errors_are_not_retried(status):
    clock = FakeClock()
    call, calls = scripted(clock, [(status, 0.01)] * 3)
    result, _, reason = run_with_retry(call, classify, POLICY, retry_allowed=True,
                                       clock=clock, sleep=clock.sleep, rng=random.Random(1))
    assert (result, reason, len(calls)) == (status, "final", 1)


def test_state_changing_operation_is_not_retried():
    clock = FakeClock()
    call, calls = scripted(clock, [(503, 0.01)] * 3)
    _, _, reason = run_with_retry(call, classify, POLICY, retry_allowed=False, clock=clock, sleep=clock.sleep)
    assert (reason, len(calls)) == ("not_retryable", 1)


def test_attempts_are_limited():
    clock = FakeClock()
    call, calls = scripted(clock, [(503, 0.01)] * 10)
    _, _, reason = run_with_retry(call, classify, POLICY, retry_allowed=True,
                                  clock=clock, sleep=clock.sleep, rng=random.Random(1))
    assert (reason, len(calls)) == ("exhausted", 3)


def test_hanging_dependency_respects_deadline():
    clock = FakeClock()
    policy = RetryPolicy(per_try_timeout=0.8, deadline=2.0, max_attempts=10, base_delay=0.05)
    call, calls = scripted(clock, [("timeout", 5.0)] * 10)
    _, _, reason = run_with_retry(call, classify, policy, retry_allowed=True,
                                  clock=clock, sleep=clock.sleep, rng=random.Random(1))
    assert reason == "deadline"
    assert clock.now <= 2.0 + 1e-9          # загальний бюджет не перевищено
    assert calls[-1] < 0.8                  # остання спроба отримала лише залишок бюджету


def test_backoff_grows_and_is_capped_with_jitter():
    rng = random.Random(42)
    caps = [min(1.0, 0.1 * 2 ** (n - 1)) for n in range(1, 7)]
    delays = [POLICY.backoff(n, rng) for n in range(1, 7)]
    assert all(0 <= d <= cap for d, cap in zip(delays, caps))
    assert len({round(d, 6) for d in delays}) == len(delays)   # jitter: паузи різні


# ---------- circuit breaker ----------
def test_breaker_opens_after_threshold_and_rejects_fast():
    clock = FakeClock()
    b = CircuitBreaker("membership", failure_threshold=3, open_timeout=5, clock=clock)
    for _ in range(3):
        assert b.allow()
        b.record_failure()
    assert b.state is BreakerState.OPEN
    assert not b.allow()                    # виклик не пропускається
    assert b.retry_after() == pytest.approx(5)


def test_success_resets_failure_counter():
    b = CircuitBreaker("m", failure_threshold=3, clock=FakeClock())
    b.record_failure(); b.record_failure(); b.record_success(); b.record_failure(); b.record_failure()
    assert b.state is BreakerState.CLOSED


def test_half_open_allows_one_probe_and_closes_on_success():
    clock = FakeClock()
    b = CircuitBreaker("m", failure_threshold=1, open_timeout=2, half_open_max_calls=1, clock=clock)
    b.allow(); b.record_failure()
    clock.now += 2
    assert b.allow() is True                # проба
    assert b.state is BreakerState.HALF_OPEN
    assert b.allow() is False               # друга одночасна проба заборонена
    b.record_success()
    assert b.state is BreakerState.CLOSED
    assert b.transitions == [("closed", "open"), ("open", "half_open"), ("half_open", "closed")]


def test_failed_probe_reopens_breaker():
    clock = FakeClock()
    b = CircuitBreaker("m", failure_threshold=1, open_timeout=2, clock=clock)
    b.allow(); b.record_failure()
    clock.now += 2
    b.allow(); b.record_failure()
    assert b.state is BreakerState.OPEN
    assert not b.allow()
    assert b.retry_after() == pytest.approx(2)


# ---------- rate limiting ----------
def test_token_bucket_limits_burst_per_key_and_refills():
    clock = FakeClock()
    limiter = TokenBucketLimiter(capacity=3, refill_per_second=1, clock=clock)
    assert [limiter.try_acquire("a")[0] for _ in range(4)] == [True, True, True, False]
    assert limiter.try_acquire("b")[0] is True          # інший клієнт має власний ліміт
    allowed, retry_after = limiter.try_acquire("a")
    assert not allowed and retry_after == pytest.approx(1.0)
    clock.now += 1
    assert limiter.try_acquire("a")[0] is True
