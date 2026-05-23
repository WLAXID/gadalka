"""Пул API-ключей с автоматической ротацией.

Загружает ключи из ``api_keys.json``, выдаёт их по round-robin,
помечает «остывающими» при ошибках 429/5xx, ведёт статистику.

Использование::

    from src.api.keypool import KeyPool

    pool = KeyPool.from_file("api_keys.json")

    async with pool.use("polygon") as key:
        # key — строка ключа Polygon.io
        response = await client.get(url, params={"apiKey": key})

При исключении с атрибутом ``status_code`` или ``response.status_code``
ключ автоматически уходит в cooldown.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from loguru import logger


# --- Длительность cooldown по типу ошибки (секунды) ---
COOLDOWN_RATE_LIMIT = 60.0       # 429 Too Many Requests
COOLDOWN_AUTH_ERROR = 3600.0     # 401/403 — возможно ключ битый, час карантина
COOLDOWN_SERVER_ERROR = 10.0     # 5xx — короткая пауза
COOLDOWN_GENERIC = 5.0           # любая другая ошибка


@dataclass
class KeyEntry:
    """Запись о ключе в пуле."""

    key: str
    label: str = "default"

    # Runtime-метрики (не сериализуются обратно в файл)
    uses: int = 0
    errors: int = 0
    last_error_at: float = 0.0
    last_error_code: int | None = None
    cooldown_until: float = 0.0  # monotonic time, до которого ключ «отдыхает»

    def is_available(self, now: float | None = None) -> bool:
        """Доступен ли ключ для использования прямо сейчас."""
        now = now if now is not None else time.monotonic()
        return self.cooldown_until <= now


class KeyPool:
    """Менеджер пула ключей с round-robin ротацией и cooldown."""

    def __init__(self, providers: dict[str, list[KeyEntry]]) -> None:
        self._providers: dict[str, list[KeyEntry]] = providers
        self._cursors: dict[str, int] = defaultdict(int)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    # ---------- Загрузка ----------

    @classmethod
    def from_file(cls, path: Path | str) -> "KeyPool":
        """Загрузить пул из JSON-файла структуры ``api_keys.json``."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Файл с ключами не найден: {path}")

        raw = json.loads(path.read_text(encoding="utf-8"))
        providers_raw = raw.get("providers", {})

        providers: dict[str, list[KeyEntry]] = {}
        for name, info in providers_raw.items():
            entries = []
            for k in info.get("keys", []):
                if not k.get("key"):
                    continue
                entries.append(KeyEntry(key=k["key"], label=k.get("label", "default")))
            if entries:
                providers[name] = entries

        logger.info(
            "Загружен KeyPool: {n} провайдеров, всего {total} ключей",
            n=len(providers),
            total=sum(len(v) for v in providers.values()),
        )
        return cls(providers)

    # ---------- Информационные методы ----------

    def providers(self) -> list[str]:
        """Имена всех загруженных провайдеров."""
        return sorted(self._providers.keys())

    def has(self, provider: str) -> bool:
        """Есть ли хоть один ключ для провайдера."""
        return bool(self._providers.get(provider))

    def count(self, provider: str) -> int:
        """Сколько всего ключей у провайдера (с учётом отдыхающих)."""
        return len(self._providers.get(provider, []))

    def available_count(self, provider: str) -> int:
        """Сколько ключей доступно прямо сейчас."""
        now = time.monotonic()
        return sum(1 for e in self._providers.get(provider, []) if e.is_available(now))

    # ---------- Получение ключа ----------

    async def acquire(self, provider: str) -> KeyEntry:
        """Получить следующий доступный ключ для провайдера.

        Round-robin курсор движется по списку. Если все ключи в cooldown,
        ждём минимального ``cooldown_until``.
        """
        if not self.has(provider):
            raise KeyError(f"Нет ключей для провайдера: {provider}")

        async with self._locks[provider]:
            keys = self._providers[provider]
            n = len(keys)
            now = time.monotonic()

            # Пытаемся найти доступный ключ за один проход
            for _ in range(n):
                idx = self._cursors[provider] % n
                self._cursors[provider] = (idx + 1) % n
                entry = keys[idx]
                if entry.is_available(now):
                    entry.uses += 1
                    return entry

            # Все ключи отдыхают — ждём ближайшего
            entry = min(keys, key=lambda e: e.cooldown_until)
            wait_s = max(0.0, entry.cooldown_until - now)
            if wait_s > 0:
                logger.warning(
                    "Все ключи {p} в cooldown, ждём {s:.1f}s",
                    p=provider,
                    s=wait_s,
                )
                await asyncio.sleep(wait_s)
            entry.uses += 1
            return entry

    def mark_failure(self, provider: str, entry: KeyEntry, status_code: int) -> None:
        """Пометить ключ как сбойный и отправить в cooldown."""
        entry.errors += 1
        entry.last_error_at = time.monotonic()
        entry.last_error_code = status_code

        if status_code == 429:
            cooldown = COOLDOWN_RATE_LIMIT
        elif status_code in (401, 403):
            cooldown = COOLDOWN_AUTH_ERROR
        elif status_code >= 500:
            cooldown = COOLDOWN_SERVER_ERROR
        else:
            cooldown = COOLDOWN_GENERIC

        entry.cooldown_until = time.monotonic() + cooldown
        logger.warning(
            "{p} key=<{label}> ошибка {code}, cooldown {s:.0f}s",
            p=provider,
            label=entry.label,
            code=status_code,
            s=cooldown,
        )

    @asynccontextmanager
    async def use(self, provider: str) -> AsyncIterator[str]:
        """Контекст-менеджер: выдаёт строку-ключ и автоматически
        регистрирует ошибки.

        Если внутри блока выбросится исключение с атрибутом ``status_code``
        или ``response.status_code`` — ключ уйдёт в cooldown.
        """
        entry = await self.acquire(provider)
        try:
            yield entry.key
        except Exception as e:  # noqa: BLE001 — нам важно поймать всё
            status = _extract_status_code(e)
            if status is not None:
                self.mark_failure(provider, entry, status)
            raise

    # ---------- Статистика ----------

    def stats(self) -> dict[str, dict[str, Any]]:
        """Сводная статистика по всем провайдерам."""
        out: dict[str, dict[str, Any]] = {}
        now = time.monotonic()
        for name, keys in self._providers.items():
            out[name] = {
                "total_keys": len(keys),
                "available": sum(1 for k in keys if k.is_available(now)),
                "in_cooldown": sum(1 for k in keys if not k.is_available(now)),
                "total_uses": sum(k.uses for k in keys),
                "total_errors": sum(k.errors for k in keys),
            }
        return out

    def stats_pretty(self) -> str:
        """Человекочитаемая таблица статистики."""
        s = self.stats()
        if not s:
            return "Пул пуст."
        lines = [
            f"{'provider':<14} {'keys':>5} {'avail':>6} {'uses':>6} {'errors':>6}",
            "-" * 44,
        ]
        for name in sorted(s.keys()):
            row = s[name]
            lines.append(
                f"{name:<14} {row['total_keys']:>5} {row['available']:>6} "
                f"{row['total_uses']:>6} {row['total_errors']:>6}"
            )
        return "\n".join(lines)


def _extract_status_code(exc: Exception) -> int | None:
    """Достать HTTP status_code из распространённых типов исключений."""
    # httpx.HTTPStatusError
    response = getattr(exc, "response", None)
    if response is not None:
        code = getattr(response, "status_code", None)
        if isinstance(code, int):
            return code
    # Прямой атрибут
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code
    return None
