"""Політики стійкості шлюзу: повтори з deadline, circuit breaker, обмеження частоти запитів.

Без залежностей від HTTP-бібліотек: годинник, затримка й випадковість передаються ззовні,
тому поведінку можна точно перевірити тестами з «фальшивим» часом.
Усі класи потокобезпечні: шлюз обробляє запити в пулі потоків.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Generic, TypeVar

log = logging.getLogger("fitshed.gateway")
T = TypeVar("T")


# ======================================================================
# Повтори з таймаутом спроби, загальним deadline, backoff і jitter
# ======================================================================
@dataclass(frozen=True)
class RetryPolicy:
    per_try_timeout: float = 0.5     # с, очікування однієї спроби
    deadline: float = 2.0            # с, бюджет усіх спроб разом із паузами
    max_attempts: int = 3            # разом з першою спробою
    base_delay: float = 0.1          # с, пауза перед 2-ю спробою (до jitter)
    max_delay: float = 1.0           # с, верхня межа паузи
    retryable_statuses: frozenset[int] = frozenset({502, 503, 504})

    def backoff(self, retry_number: int, rng: random.Random) -> float:
        """Exponential backoff з full jitter: випадкова пауза від 0 до base·2^(n−1), не більше max_delay."""
        cap = min(self.max_delay, self.base_delay * 2 ** (retry_number - 1))
        return rng.uniform(0, cap)


@dataclass
class Attempt(Generic[T]):
    number: int
    timeout: float
    outcome: str            # ok | retryable | final | timeout | connection_error
    result: T | None = None
    elapsed: float = 0.0


class TransientError(Exception):
    """Таймаут або обрив з'єднання: виклик можна повторити, якщо операція безпечна."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind    # timeout | connection_error


def run_with_retry(call: Callable[[float], T], classify: Callable[[T], str], policy: RetryPolicy, *,
                   retry_allowed: bool, clock: Callable[[], float] = time.monotonic,
                   sleep: Callable[[float], None] = time.sleep, rng: random.Random | None = None,
                   before_attempt: Callable[[], bool] = lambda: True,
                   on_attempt: Callable[[Attempt], None] = lambda a: None) -> tuple[T | None, list[Attempt], str]:
    """Виконує call(timeout) з повторами.

    classify(result) → «ok», «retryable» (наприклад 503) або «final» (наприклад 404).
    before_attempt() → False, якщо спробу робити не можна (відкрився breaker).
    Повертає (останній результат, спроби, причина завершення):
      ok | final | exhausted (спроби вичерпано) | deadline | not_retryable | breaker_open.
    """
    rng = rng or random.Random()
    started = clock()
    attempts: list[Attempt] = []
    last: T | None = None
    while True:
        remaining = policy.deadline - (clock() - started)
        if remaining <= 0:
            return last, attempts, "deadline"
        if not before_attempt():
            return last, attempts, "breaker_open"

        number = len(attempts) + 1
        timeout = min(policy.per_try_timeout, remaining)
        t0 = clock()
        try:
            last = call(timeout)
            outcome = classify(last)
        except TransientError as error:
            last, outcome = None, error.kind
        attempt = Attempt(number, timeout, outcome, last, clock() - t0)
        attempts.append(attempt)
        on_attempt(attempt)

        if outcome in ("ok", "final"):
            return last, attempts, outcome
        if not retry_allowed:
            return last, attempts, "not_retryable"
        if number >= policy.max_attempts:
            return last, attempts, "exhausted"

        pause = policy.backoff(number, rng)
        if clock() - started + pause >= policy.deadline:
            return last, attempts, "deadline"   # пауза з'їла б увесь залишок бюджету
        sleep(pause)


# ======================================================================
# Circuit breaker
# ======================================================================
class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int = 5        # стільки відмов поспіль відкривають breaker
    open_timeout: float = 3.0         # с у стані open до першої проби
    half_open_max_calls: int = 1      # скільки пробних викликів дозволено в half-open
    clock: Callable[[], float] = time.monotonic
    state: BreakerState = BreakerState.CLOSED
    transitions: list[tuple[str, str]] = field(default_factory=list)
    _failures: int = 0
    _opened_at: float = 0.0
    _half_open_in_flight: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def allow(self) -> bool:
        """Чи можна зараз звертатися до залежності."""
        with self._lock:
            if self.state is BreakerState.OPEN and self.clock() - self._opened_at >= self.open_timeout:
                self._move(BreakerState.HALF_OPEN)
            if self.state is BreakerState.CLOSED:
                return True
            if self.state is BreakerState.HALF_OPEN and self._half_open_in_flight < self.half_open_max_calls:
                self._half_open_in_flight += 1
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            if self.state is BreakerState.HALF_OPEN:
                self._half_open_in_flight = 0
                self._move(BreakerState.CLOSED)

    def record_failure(self) -> None:
        with self._lock:
            if self.state is BreakerState.HALF_OPEN:
                self._half_open_in_flight = 0
                self._open()
                return
            self._failures += 1
            if self.state is BreakerState.CLOSED and self._failures >= self.failure_threshold:
                self._open()

    def retry_after(self) -> float:
        with self._lock:
            return max(0.0, self.open_timeout - (self.clock() - self._opened_at))

    def reset(self) -> None:
        with self._lock:
            self.state, self._failures, self._half_open_in_flight = BreakerState.CLOSED, 0, 0
            self.transitions.clear()

    def _open(self) -> None:
        self._opened_at = self.clock()
        self._move(BreakerState.OPEN)

    def _move(self, new: BreakerState) -> None:
        old, self.state = self.state, new
        self.transitions.append((old.value, new.value))
        log.warning("breaker=%s state=%s->%s failures=%d", self.name, old.value, new.value, self._failures)
        if new is BreakerState.CLOSED:
            self._failures = 0


# ======================================================================
# Rate limiting: token bucket на кожен ключ клієнта
# ======================================================================
@dataclass
class TokenBucketLimiter:
    capacity: int = 10                # скільки запитів можна зробити «пачкою»
    refill_per_second: float = 5.0    # з якою швидкістю відновлюються токени
    clock: Callable[[], float] = time.monotonic
    _buckets: dict[str, tuple[float, float]] = field(default_factory=dict)   # ключ → (токени, час)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def try_acquire(self, key: str) -> tuple[bool, float]:
        """(дозволено, через скільки секунд з'явиться наступний токен)."""
        with self._lock:
            now = self.clock()
            tokens, last = self._buckets.get(key, (float(self.capacity), now))
            tokens = min(self.capacity, tokens + (now - last) * self.refill_per_second)
            if tokens >= 1:
                self._buckets[key] = (tokens - 1, now)
                return True, 0.0
            self._buckets[key] = (tokens, now)
            return False, (1 - tokens) / self.refill_per_second

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()
