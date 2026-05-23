"""Простой async token bucket для рейт-лимитинга.

Используется в HTTP-клиентах, чтобы не получать 429.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass


@dataclass
class TokenBucket:
    """Классический алгоритм с непрерывным пополнением.

    Параметры:
    - ``rate``      — пополнение, токенов в секунду
    - ``capacity``  — максимальный burst (по умолчанию = rate)
    """

    rate: float
    capacity: float | None = None

    def __post_init__(self) -> None:
        if self.capacity is None:
            self.capacity = self.rate
        self._tokens = float(self.capacity)
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, n: float = 1.0) -> None:
        """Заблокировать выполнение, пока не доступно ``n`` токенов.

        Можно вызывать конкурентно — внутри есть Lock.
        """
        if n > (self.capacity or self.rate):
            raise ValueError(
                f"acquire({n}) > capacity={self.capacity}; такой запрос никогда не пройдёт"
            )
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._last
                self._tokens = min(
                    float(self.capacity or self.rate),
                    self._tokens + elapsed * self.rate,
                )
                self._last = now
                if self._tokens >= n:
                    self._tokens -= n
                    return
                wait = (n - self._tokens) / self.rate
                await asyncio.sleep(wait)

    @property
    def available(self) -> float:
        """Текущее число токенов (без побочных эффектов; приблизительно)."""
        now = time.monotonic()
        return min(
            float(self.capacity or self.rate),
            self._tokens + (now - self._last) * self.rate,
        )
