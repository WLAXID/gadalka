"""HTTP-клиент для трёх API Polymarket: Gamma, CLOB, Data.

Использует ``httpx.AsyncClient``, token bucket per host, retry на 429/5xx,
опциональный файловый кэш.

Использование::

    from src.api.polymarket import PolymarketClient

    async with PolymarketClient(cache_dir="data/cache/polymarket") as pm:
        markets = await pm.gamma_markets(closed=True, limit=10)
        book = await pm.clob_book(token_id="...")
        ph = await pm.timeseries_prices(market="...", interval="1h")
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from loguru import logger
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.api.cache import CachedResponse, FileCache
from src.api.ratelimit import TokenBucket


# --- Базовые URL ---
GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"
DATA_BASE = "https://data-api.polymarket.com"


# --- Рейт-лимиты (с буфером ~70% от advertised) ---
RATE_LIMITS: dict[str, float] = {
    GAMMA_BASE: 280.0,   # 4000/10s = 400/s, берём 70% → 280/s
    CLOB_BASE: 630.0,    # 9000/10s = 900/s, берём 70% → 630/s
    DATA_BASE: 70.0,     # 1000/10s = 100/s, берём 70% → 70/s
}


class PolymarketError(Exception):
    """Базовое исключение клиента Polymarket."""


class PolymarketClient:
    """Единый клиент к Gamma / CLOB / Data API.

    Все методы — read-only, без auth. Для write-ops (Фаза 3+)
    используем py-clob-client-v2 отдельно.
    """

    def __init__(
        self,
        *,
        cache_dir: Path | str | None = None,
        timeout: float = 30.0,
        max_retries: int = 4,
        trust_env: bool = False,
    ) -> None:
        self._timeout = httpx.Timeout(timeout, connect=10.0)
        self._limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
        self._max_retries = max_retries
        self._trust_env = trust_env

        # Token-bucket per host
        self._buckets: dict[str, TokenBucket] = {
            host: TokenBucket(rate=rate, capacity=rate * 2)
            for host, rate in RATE_LIMITS.items()
        }

        # Кэш — опционально
        self._cache: FileCache | None = (
            FileCache(cache_dir) if cache_dir is not None else None
        )
        self._client: httpx.AsyncClient | None = None

    # ---------- Lifecycle ----------

    async def __aenter__(self) -> "PolymarketClient":
        self._client = httpx.AsyncClient(
            timeout=self._timeout,
            limits=self._limits,
            trust_env=self._trust_env,
            headers={"User-Agent": "gadalka/0.1 (research)"},
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ---------- Внутренняя кухня ----------

    def _bucket_for(self, url: str) -> TokenBucket | None:
        for host, bucket in self._buckets.items():
            if url.startswith(host):
                return bucket
        return None

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        json_body: Any = None,
        use_cache: bool = True,
    ) -> dict | list:
        if self._client is None:
            raise RuntimeError("Клиент не открыт — используй async with")

        # Кэш только для GET
        cache_key = None
        if use_cache and self._cache is not None and method.upper() == "GET":
            cache_key = FileCache.make_key(method, url, params, json_body)
            cached = self._cache.get(cache_key)
            if cached is not None:
                logger.debug(f"[cache hit] {method} {url}")
                return cached.json()

        bucket = self._bucket_for(url)
        if bucket is not None:
            await bucket.acquire()

        async def _do() -> httpx.Response:
            r = await self._client.request(  # type: ignore[union-attr]
                method, url, params=params, json=json_body
            )
            # Перевод 429/5xx в исключение, чтобы retry мог сработать
            if r.status_code == 429 or 500 <= r.status_code < 600:
                raise PolymarketError(
                    f"{method} {url} → {r.status_code}: {r.text[:200]}"
                )
            return r

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._max_retries),
                wait=wait_exponential(multiplier=1, min=1, max=30),
                retry=retry_if_exception_type(PolymarketError),
                reraise=True,
            ):
                with attempt:
                    response = await _do()
        except RetryError as e:
            raise PolymarketError(f"retries exhausted: {e}") from e

        if response.status_code >= 400:
            raise PolymarketError(
                f"{method} {url} → {response.status_code}: {response.text[:200]}"
            )

        data = response.json()

        # Кэшируем только успешные GET
        if cache_key and self._cache is not None:
            self._cache.put(
                cache_key,
                CachedResponse(
                    status_code=response.status_code,
                    body=response.text,
                    headers=dict(response.headers),
                ),
            )

        return data

    # ============================================================
    # GAMMA — metadata рынков/событий, search, TimeSeries
    # ============================================================

    async def gamma_markets(
        self,
        *,
        closed: bool | None = None,
        active: bool | None = None,
        limit: int = 100,
        offset: int = 0,
        order: str = "endDate",
        ascending: bool = False,
        tag_id: int | None = None,
        archived: bool | None = None,
        condition_ids: list[str] | None = None,
    ) -> list[dict]:
        """GET /markets — список рынков с фильтрами."""
        params: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "order": order,
            "ascending": str(ascending).lower(),
        }
        if closed is not None:
            params["closed"] = str(closed).lower()
        if active is not None:
            params["active"] = str(active).lower()
        if archived is not None:
            params["archived"] = str(archived).lower()
        if tag_id is not None:
            params["tag_id"] = tag_id
        if condition_ids:
            params["condition_ids"] = ",".join(condition_ids)
        data = await self._request("GET", f"{GAMMA_BASE}/markets", params=params)
        # Gamma /markets возвращает массив
        return data if isinstance(data, list) else []

    async def gamma_market_by_id(self, market_id: str | int) -> dict:
        """GET /markets/{id} — детали одного рынка."""
        return await self._request("GET", f"{GAMMA_BASE}/markets/{market_id}")

    async def gamma_events(
        self,
        *,
        closed: bool | None = None,
        active: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """GET /events — список событий (один event объединяет несколько markets)."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if closed is not None:
            params["closed"] = str(closed).lower()
        if active is not None:
            params["active"] = str(active).lower()
        data = await self._request("GET", f"{GAMMA_BASE}/events", params=params)
        return data if isinstance(data, list) else []

    async def gamma_event_by_id(self, event_id: str | int) -> dict:
        """GET /events/{id}."""
        return await self._request("GET", f"{GAMMA_BASE}/events/{event_id}")

    # ============================================================
    # TimeSeries (на CLOB, не Gamma!)
    # ============================================================

    async def clob_prices_history(
        self,
        *,
        market: str,
        interval: str = "1h",
        start_ts: int | None = None,
        end_ts: int | None = None,
        fidelity: int | None = None,
    ) -> list[dict]:
        """GET clob/prices-history — историческая цена outcome-токена.

        - ``market`` — token_id (asset_id из CLOB), НЕ condition_id
        - ``interval`` — ``1m``, ``1h``, ``6h``, ``1d``, ``max``
        - возвращает список точек ``{"t": unix_ts, "p": price 0..1}``

        Важно: для разных interval Polymarket даёт разное окно по умолчанию
        (например, для ``1h`` это последние ~7 дней). Для полного историзма
        нужно явно передавать ``start_ts``/``end_ts``.
        """
        params: dict[str, Any] = {"market": market, "interval": interval}
        if start_ts is not None:
            params["startTs"] = start_ts
        if end_ts is not None:
            params["endTs"] = end_ts
        if fidelity is not None:
            params["fidelity"] = fidelity
        data = await self._request(
            "GET", f"{CLOB_BASE}/prices-history", params=params
        )
        if isinstance(data, dict):
            return data.get("history", []) or []
        return data if isinstance(data, list) else []

    # ============================================================
    # CLOB — orderbook, current price, recent trades
    # ============================================================

    async def clob_markets(
        self, *, next_cursor: str | None = None
    ) -> dict:
        """GET /markets — пагинируемый список через CLOB.

        В отличие от Gamma, CLOB отдаёт ``{"data": [...], "next_cursor": ...}``.
        """
        params: dict[str, Any] = {}
        if next_cursor:
            params["next_cursor"] = next_cursor
        return await self._request("GET", f"{CLOB_BASE}/markets", params=params)

    async def clob_market(self, condition_id: str) -> dict:
        """GET /markets/{condition_id} — детали из CLOB."""
        return await self._request("GET", f"{CLOB_BASE}/markets/{condition_id}")

    async def clob_book(self, token_id: str) -> dict:
        """GET /book?token_id=... — текущий orderbook."""
        return await self._request(
            "GET", f"{CLOB_BASE}/book", params={"token_id": token_id}
        )

    async def clob_price(self, token_id: str, side: str = "buy") -> dict:
        """GET /price?token_id=...&side=... — best price for side."""
        return await self._request(
            "GET",
            f"{CLOB_BASE}/price",
            params={"token_id": token_id, "side": side},
        )

    async def clob_midpoint(self, token_id: str) -> dict:
        """GET /midpoint?token_id=..."""
        return await self._request(
            "GET", f"{CLOB_BASE}/midpoint", params={"token_id": token_id}
        )

    async def clob_trades(self, *, market: str | None = None) -> list[dict] | dict:
        """GET /trades — недавние сделки CLOB."""
        params: dict[str, Any] = {}
        if market:
            params["market"] = market
        return await self._request("GET", f"{CLOB_BASE}/trades", params=params)

    # ============================================================
    # Data API — historical trades, positions, holders, value
    # ============================================================

    async def data_trades(
        self,
        *,
        market: str | None = None,
        user: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """GET /trades — исторические сделки."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if market:
            params["market"] = market
        if user:
            params["user"] = user
        data = await self._request("GET", f"{DATA_BASE}/trades", params=params)
        return data if isinstance(data, list) else []

    async def data_positions(
        self,
        *,
        user: str,
        market: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """GET /positions — позиции кошелька."""
        params: dict[str, Any] = {"user": user, "limit": limit}
        if market:
            params["market"] = market
        data = await self._request("GET", f"{DATA_BASE}/positions", params=params)
        return data if isinstance(data, list) else []

    async def data_holders(self, market: str, limit: int = 100) -> list[dict]:
        """GET /holders — крупнейшие держатели позиций по рынку."""
        data = await self._request(
            "GET", f"{DATA_BASE}/holders", params={"market": market, "limit": limit}
        )
        return data if isinstance(data, list) else []

    async def data_value(self, user: str) -> dict | list:
        """GET /value — суммарный объём позиций кошелька."""
        return await self._request("GET", f"{DATA_BASE}/value", params={"user": user})


# ---------- Удобный фабричный wrapper ----------


@asynccontextmanager
async def polymarket(
    cache_dir: Path | str | None = "data/cache/polymarket",
) -> AsyncIterator[PolymarketClient]:
    """Удобный fact-method для контекста.

    Пример::

        async with polymarket() as pm:
            markets = await pm.gamma_markets(closed=True, limit=10)
    """
    async with PolymarketClient(cache_dir=cache_dir) as client:
        yield client
