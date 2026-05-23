"""Probe всех API-ключей в пуле — определить тариф (free / paid).

Делает 1-2 лёгких запроса на ключ, чтобы понять:
- доступен ли ключ вообще
- какой план (free / Starter / Business / etc)
- какие платные фичи разблокированы
- какие rate-limit лимиты

Запуск::

    python scripts/probe_keys.py                # все core+useful провайдеры
    python scripts/probe_keys.py --provider newsapi  # только один

Результаты сохраняются в ``data/cache/probe_results.json``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

# Windows консоль — UTF-8 для emoji/кириллицы
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from loguru import logger  # noqa: E402

from src.api.keypool import KeyPool  # noqa: E402


# ============================================================================
# Структуры
# ============================================================================


@dataclass
class KeyProbe:
    """Результат пробы одного ключа."""

    label: str
    tail: str  # последние 6 символов ключа
    ok: bool = False
    tier: str = "unknown"
    paid_features: list[str] = field(default_factory=list)
    rate_limit: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


# ============================================================================
# Утилиты
# ============================================================================


def tail(key: str, n: int = 8) -> str:
    """Безопасный хвост ключа для логов."""
    if not key:
        return "(empty)"
    return key[-n:] if len(key) > n else key


# ============================================================================
# Probe-функции по провайдерам
# ============================================================================


async def probe_polygon(client: httpx.AsyncClient, key: str) -> KeyProbe:
    """Polygon.io — Basic vs Starter+ (Crypto/Stocks/Options)."""
    probe = KeyProbe(label="default", tail=tail(key))
    try:
        # Базовый запрос — должен работать на всех планах
        r = await client.get(
            "https://api.polygon.io/v3/reference/tickers",
            params={"apiKey": key, "limit": 1},
            timeout=10,
        )
        if r.status_code != 200:
            probe.error = f"basic probe {r.status_code}: {r.text[:120]}"
            return probe
        probe.ok = True

        # Попытка получить минутную свечу BTC за вчера (paid feature)
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        r_min = await client.get(
            f"https://api.polygon.io/v2/aggs/ticker/X:BTCUSD/range/1/minute/{yesterday}/{yesterday}",
            params={"apiKey": key, "limit": 10},
            timeout=10,
        )
        if r_min.status_code == 200:
            data = r_min.json()
            if data.get("resultsCount", 0) > 0:
                probe.paid_features.append("crypto_minute_aggs")
        elif r_min.status_code == 403:
            # Free план crypto заблокирован
            pass

        # Реверс: попытка получить старые данные (>2 лет — обычно free 2y limit)
        old_date = (datetime.now(timezone.utc) - timedelta(days=900)).strftime("%Y-%m-%d")
        r_old = await client.get(
            f"https://api.polygon.io/v2/aggs/ticker/X:BTCUSD/range/1/day/{old_date}/{old_date}",
            params={"apiKey": key, "limit": 1},
            timeout=10,
        )
        if r_old.status_code == 200 and r_old.json().get("resultsCount", 0) > 0:
            probe.paid_features.append("crypto_historical_3y+")

        if "crypto_minute_aggs" in probe.paid_features:
            probe.tier = "Crypto Starter+"
        elif probe.ok:
            probe.tier = "Basic (free)"
    except Exception as e:
        probe.error = f"{type(e).__name__}: {e}"
    return probe


async def probe_alchemy(client: httpx.AsyncClient, key: str) -> KeyProbe:
    """Alchemy — Polygon RPC. Ключ — API key, URL строим сами."""
    probe = KeyProbe(label="default", tail=tail(key))
    # Целимся в Polygon mainnet — наш основной use case
    url = f"https://polygon-mainnet.g.alchemy.com/v2/{key}"
    try:
        # Базовый JSON-RPC eth_chainId
        r = await client.post(
            url,
            json={"jsonrpc": "2.0", "method": "eth_chainId", "params": [], "id": 1},
            timeout=10,
        )
        if r.status_code != 200:
            probe.error = f"rpc probe {r.status_code}: {r.text[:120]}"
            return probe
        data = r.json()
        if "result" not in data:
            probe.error = f"no result: {str(data)[:120]}"
            return probe
        probe.ok = True

        # Archive node check — попытка eth_getBalance на старом блоке
        old_block = "0x100000"  # ~блок 1M, точно архив
        r_arch = await client.post(
            url,
            json={
                "jsonrpc": "2.0",
                "method": "eth_getBalance",
                "params": ["0x0000000000000000000000000000000000000000", old_block],
                "id": 2,
            },
            timeout=10,
        )
        if r_arch.status_code == 200:
            data_arch = r_arch.json()
            if "result" in data_arch:
                probe.paid_features.append("archive")

        # Compute units rate-limit (Alchemy header)
        for hdr in ("x-ratelimit-limit", "x-ratelimit-remaining"):
            if hdr in r.headers:
                probe.rate_limit[hdr] = r.headers[hdr]

        if "archive" in probe.paid_features:
            probe.tier = "Free+archive (или paid)"
        else:
            probe.tier = "Free (no archive)"
    except Exception as e:
        probe.error = f"{type(e).__name__}: {e}"
    return probe


async def probe_chainstack(client: httpx.AsyncClient, url: str) -> KeyProbe:
    """Chainstack — Polygon RPC. Ключ — полный URL."""
    probe = KeyProbe(label="default", tail=tail(url))
    try:
        r = await client.post(
            url,
            json={"jsonrpc": "2.0", "method": "eth_chainId", "params": [], "id": 1},
            timeout=10,
        )
        if r.status_code != 200:
            probe.error = f"rpc probe {r.status_code}: {r.text[:120]}"
            return probe
        data = r.json()
        if "result" not in data:
            probe.error = f"no result: {str(data)[:120]}"
            return probe
        probe.ok = True

        # Archive
        r_arch = await client.post(
            url,
            json={
                "jsonrpc": "2.0",
                "method": "eth_getBalance",
                "params": ["0x0000000000000000000000000000000000000000", "0x100000"],
                "id": 2,
            },
            timeout=10,
        )
        if r_arch.status_code == 200 and "result" in r_arch.json():
            probe.paid_features.append("archive")

        probe.tier = "with archive" if "archive" in probe.paid_features else "Developer (no archive)"
    except Exception as e:
        probe.error = f"{type(e).__name__}: {e}"
    return probe


async def probe_newsapi(client: httpx.AsyncClient, key: str) -> KeyProbe:
    """NewsAPI — Developer (1 month) vs Business (5 years).

    Game-changer для gadalka — нужны исторические новости для бэктеста news-lag.
    """
    probe = KeyProbe(label="default", tail=tail(key))
    try:
        # Базовый запрос — сегодня
        r = await client.get(
            "https://newsapi.org/v2/everything",
            params={"q": "test", "pageSize": 1, "apiKey": key},
            timeout=10,
        )
        if r.status_code != 200:
            probe.error = f"basic probe {r.status_code}: {r.text[:120]}"
            return probe
        probe.ok = True

        # КРИТИЧНО: попытка получить новости >1 месяца назад
        old_date = (datetime.now(timezone.utc) - timedelta(days=400)).strftime("%Y-%m-%d")
        r_old = await client.get(
            "https://newsapi.org/v2/everything",
            params={"q": "test", "from": old_date, "pageSize": 1, "apiKey": key},
            timeout=10,
        )
        if r_old.status_code == 200:
            probe.paid_features.append("historical_1y+")
            probe.tier = "Business (5y history) ⭐"
        elif r_old.status_code == 426:
            probe.tier = "Developer (1 month)"
        else:
            probe.tier = f"Developer? ({r_old.status_code})"
    except Exception as e:
        probe.error = f"{type(e).__name__}: {e}"
    return probe


async def probe_tavily(client: httpx.AsyncClient, key: str) -> KeyProbe:
    """Tavily — Researcher vs Pro (extract / advanced search depth)."""
    probe = KeyProbe(label="default", tail=tail(key))
    try:
        # Basic search probe
        r = await client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": key,
                "query": "test",
                "max_results": 1,
                "search_depth": "basic",
            },
            timeout=15,
        )
        if r.status_code not in (200, 201):
            probe.error = f"basic probe {r.status_code}: {r.text[:120]}"
            return probe
        probe.ok = True

        # Extract API — Pro фича
        r_extract = await client.post(
            "https://api.tavily.com/extract",
            json={"api_key": key, "urls": ["https://example.com"]},
            timeout=15,
        )
        if r_extract.status_code in (200, 201):
            probe.paid_features.append("extract")
            probe.tier = "Pro+ (extract)"
        elif r_extract.status_code == 403:
            probe.tier = "Researcher (free)"
        else:
            probe.tier = f"Researcher? ({r_extract.status_code})"
    except Exception as e:
        probe.error = f"{type(e).__name__}: {e}"
    return probe


async def probe_perplexity(client: httpx.AsyncClient, key: str) -> KeyProbe:
    """Perplexity — sonar / sonar-pro / online models."""
    probe = KeyProbe(label="default", tail=tail(key))
    try:
        # Минимальный chat с sonar (online model)
        r = await client.post(
            "https://api.perplexity.ai/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": "sonar",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 16,
            },
            timeout=20,
        )
        if r.status_code == 200:
            probe.ok = True
            probe.tier = "sonar OK"
            data = r.json()
            if "citations" in data or "search_results" in data:
                probe.paid_features.append("online_search")
        elif r.status_code == 401:
            probe.error = "401 invalid key"
        elif r.status_code == 402:
            probe.error = "402 payment required (no balance)"
        else:
            probe.error = f"{r.status_code}: {r.text[:120]}"
    except Exception as e:
        probe.error = f"{type(e).__name__}: {e}"
    return probe


async def probe_odds_api(client: httpx.AsyncClient, key: str) -> KeyProbe:
    """The Odds API — все тарифы дают одинаковый функционал, отличие в квотах."""
    probe = KeyProbe(label="default", tail=tail(key))
    try:
        r = await client.get(
            "https://api.the-odds-api.com/v4/sports",
            params={"apiKey": key},
            timeout=10,
        )
        if r.status_code != 200:
            probe.error = f"{r.status_code}: {r.text[:120]}"
            return probe
        probe.ok = True
        # Квоты в headers
        for hdr in (
            "x-requests-remaining",
            "x-requests-used",
            "x-requests-last",
        ):
            if hdr in r.headers:
                probe.rate_limit[hdr] = r.headers[hdr]
        used = int(probe.rate_limit.get("x-requests-used", "0"))
        remaining = int(probe.rate_limit.get("x-requests-remaining", "0"))
        total = used + remaining
        probe.tier = f"~{total} req/mo (remaining {remaining})"
    except Exception as e:
        probe.error = f"{type(e).__name__}: {e}"
    return probe


async def probe_deepseek(client: httpx.AsyncClient, key: str) -> KeyProbe:
    """DeepSeek — /user/balance официальный endpoint."""
    probe = KeyProbe(label="default", tail=tail(key))
    try:
        r = await client.get(
            "https://api.deepseek.com/user/balance",
            headers={"Authorization": f"Bearer {key}"},
            timeout=15,
        )
        if r.status_code == 200:
            probe.ok = True
            data = r.json()
            infos = data.get("balance_infos", [])
            if infos:
                first = infos[0]
                probe.tier = (
                    f"available={first.get('total_balance')} "
                    f"{first.get('currency')}"
                )
                granted = first.get("granted_balance", "0")
                topped = first.get("topped_up_balance", "0")
                probe.rate_limit = {
                    "granted": granted,
                    "topped_up": topped,
                }
                if float(topped or 0) > 0:
                    probe.paid_features.append("paid_balance")
            else:
                probe.tier = "balance unknown"
        elif r.status_code == 401:
            probe.error = "401 invalid key"
        else:
            probe.error = f"{r.status_code}: {r.text[:120]}"
    except Exception as e:
        probe.error = f"{type(e).__name__}: {e}"
    return probe


async def probe_openai(client: httpx.AsyncClient, key: str) -> KeyProbe:
    """OpenAI — /v1/models + rate-limit headers."""
    probe = KeyProbe(label="default", tail=tail(key))
    try:
        r = await client.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=15,
        )
        if r.status_code == 200:
            probe.ok = True
            data = r.json()
            models = [m["id"] for m in data.get("data", [])]
            # Премиум модели
            premium = [m for m in models if "gpt-4" in m or "o1" in m or "o3" in m]
            if premium:
                probe.paid_features.append("gpt4_access")
                probe.tier = f"has gpt-4 ({len(premium)} variants)"
            else:
                probe.tier = "basic (no gpt-4)"
        elif r.status_code == 401:
            probe.error = "401 invalid key"
        else:
            probe.error = f"{r.status_code}: {r.text[:120]}"
    except Exception as e:
        probe.error = f"{type(e).__name__}: {e}"
    return probe


async def probe_coingecko(client: httpx.AsyncClient, key: str) -> KeyProbe:
    """CoinGecko — Demo (CG-* префикс) / Analyst / Lite / Pro."""
    probe = KeyProbe(label="default", tail=tail(key))
    try:
        # CG-* — обычно Demo ключи. Пробуем demo первым.
        r_demo = await client.get(
            "https://api.coingecko.com/api/v3/ping",
            headers={"x-cg-demo-api-key": key},
            timeout=10,
        )
        if r_demo.status_code == 200:
            probe.ok = True
            probe.tier = "Demo (free)"
            # Проверим месячный лимит — Demo даёт 10k calls/мес
            # Пробуем pro endpoint — если 200, значит уровень выше
            r_pro = await client.get(
                "https://pro-api.coingecko.com/api/v3/ping",
                headers={"x-cg-pro-api-key": key},
                timeout=10,
            )
            if r_pro.status_code == 200:
                probe.tier = "Pro/Analyst+"
                probe.paid_features.append("pro_api")
            return probe

        # Fallback: попробовать Pro
        r_pro = await client.get(
            "https://pro-api.coingecko.com/api/v3/ping",
            headers={"x-cg-pro-api-key": key},
            timeout=10,
        )
        if r_pro.status_code == 200:
            probe.ok = True
            probe.tier = "Pro/Analyst+"
            probe.paid_features.append("pro_api")
        else:
            probe.error = (
                f"demo:{r_demo.status_code}, pro:{r_pro.status_code} | "
                f"{r_demo.text[:80]}"
            )
    except Exception as e:
        probe.error = f"{type(e).__name__}: {e}"
    return probe


async def probe_fred(client: httpx.AsyncClient, key: str) -> KeyProbe:
    """FRED — всегда free, проверяем что ключ валиден."""
    probe = KeyProbe(label="default", tail=tail(key))
    try:
        r = await client.get(
            "https://api.stlouisfed.org/fred/series",
            params={"series_id": "GDP", "api_key": key, "file_type": "json"},
            timeout=10,
        )
        if r.status_code == 200:
            probe.ok = True
            probe.tier = "Free (FRED)"
        else:
            probe.error = f"{r.status_code}: {r.text[:120]}"
    except Exception as e:
        probe.error = f"{type(e).__name__}: {e}"
    return probe


# ============================================================================
# Карта provider → probe
# ============================================================================

PROBES: dict[str, Callable[[httpx.AsyncClient, str], Awaitable[KeyProbe]]] = {
    "polygon": probe_polygon,
    "alchemy": probe_alchemy,
    "chainstack": probe_chainstack,
    "newsapi": probe_newsapi,
    "tavily": probe_tavily,
    "perplexity": probe_perplexity,
    "odds_api": probe_odds_api,
    "deepseek": probe_deepseek,
    "openai": probe_openai,
    "coingecko": probe_coingecko,
    "fred": probe_fred,
}


# ============================================================================
# Runner
# ============================================================================


async def probe_provider(
    pool: KeyPool,
    provider: str,
    client: httpx.AsyncClient,
    max_concurrency: int = 5,
    max_keys: int | None = None,
) -> list[KeyProbe]:
    """Проба всех ключей одного провайдера с ограничением concurrency."""
    probe_fn = PROBES.get(provider)
    if probe_fn is None:
        logger.warning(f"Нет probe-функции для {provider}")
        return []

    keys = pool._providers[provider][: max_keys] if max_keys else pool._providers[provider]  # noqa: SLF001
    sem = asyncio.Semaphore(max_concurrency)
    results: list[KeyProbe] = []

    async def _worker(entry):
        async with sem:
            try:
                r = await probe_fn(client, entry.key)
                return r
            except Exception as e:
                return KeyProbe(
                    label=entry.label,
                    tail=tail(entry.key),
                    error=f"unhandled: {type(e).__name__}: {e}",
                )

    tasks = [_worker(e) for e in keys]
    for coro in asyncio.as_completed(tasks):
        r = await coro
        results.append(r)
    return results


def summarize(provider: str, results: list[KeyProbe]) -> str:
    """Текстовая сводка по провайдеру."""
    total = len(results)
    ok = sum(1 for r in results if r.ok)
    errors = total - ok
    tiers: dict[str, int] = {}
    paid_features_count: dict[str, int] = {}
    for r in results:
        tiers[r.tier] = tiers.get(r.tier, 0) + 1
        for f in r.paid_features:
            paid_features_count[f] = paid_features_count.get(f, 0) + 1

    lines = [
        f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"  {provider}   ({ok}/{total} ok, {errors} errors)",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    if tiers:
        lines.append("  Распределение по тирам:")
        for tier, count in sorted(tiers.items(), key=lambda x: -x[1]):
            lines.append(f"    [{count:>3}]  {tier}")
    if paid_features_count:
        lines.append("  Платные фичи:")
        for f, count in sorted(paid_features_count.items(), key=lambda x: -x[1]):
            lines.append(f"    [{count:>3}]  {f}")
    if errors > 0:
        # Первые 3 уникальные ошибки
        unique_errors: dict[str, int] = {}
        for r in results:
            if r.error:
                unique_errors[r.error[:80]] = unique_errors.get(r.error[:80], 0) + 1
        if unique_errors:
            lines.append("  Топ ошибок:")
            for err, count in sorted(unique_errors.items(), key=lambda x: -x[1])[:3]:
                lines.append(f"    [{count:>3}]  {err}")
    return "\n".join(lines)


async def main(provider_filter: str | None, max_keys: int | None) -> None:
    keys_file = ROOT / "api_keys.json"
    if not keys_file.exists():
        logger.error("Файл api_keys.json не найден")
        sys.exit(1)

    pool = KeyPool.from_file(keys_file)

    targets = (
        [provider_filter] if provider_filter else [p for p in PROBES if pool.has(p)]
    )
    logger.info(f"Probe для провайдеров: {targets}")

    all_results: dict[str, list[dict]] = {}

    # Один общий HTTP-клиент с разумными настройками
    # trust_env=False — игнорируем системные настройки прокси
    # (socks4 из Windows реестра не поддерживается httpx)
    timeout = httpx.Timeout(30.0, connect=10.0)
    limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)

    async with httpx.AsyncClient(
        timeout=timeout, limits=limits, trust_env=False
    ) as client:
        for provider in targets:
            if not pool.has(provider):
                logger.warning(f"{provider}: нет ключей в пуле")
                continue
            t0 = time.time()
            results = await probe_provider(
                pool, provider, client, max_concurrency=5, max_keys=max_keys
            )
            dt = time.time() - t0
            print(summarize(provider, results))
            print(f"  ⏱  {dt:.1f}s\n")
            all_results[provider] = [asdict(r) for r in results]

    # Сохранить результаты
    out_path = ROOT / "data" / "cache" / "probe_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "results": all_results,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"\n💾 Сохранено: {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", help="Пробить только этого провайдера")
    parser.add_argument("--max-keys", type=int, help="Лимит ключей на провайдера")
    args = parser.parse_args()
    asyncio.run(main(args.provider, args.max_keys))
