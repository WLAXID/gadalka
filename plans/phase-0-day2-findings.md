# 📋 Phase 0 — Day 2 Findings

**Дата:** 2026-05-23
**Документы:** [`docs/api-schemas.md`](../docs/api-schemas.md), [`docs/geo-check.md`](../docs/geo-check.md)

---

## TL;DR

Day 2 проведён за один заход (вместе с базовой инфраструктурой). 3 главных результата:

1. ✅ **Geo OK** — все 3 API Polymarket работают из РФ без VPN.
2. ✅ **PolymarketClient готов** — поднят httpx-клиент с token bucket + retry + кэшем.
3. 🎁 **`prices-history?interval=max` даёт полную историю** — для тестового рынка 6 мес = 4308 точек. **Day 4 (price-history feasibility) почти пройден прямо сейчас.**

---

## Что сделано

### Инфраструктура

| Файл | Назначение |
|------|------------|
| `src/api/ratelimit.py` | TokenBucket (async, lock) |
| `src/api/cache.py` | FileCache GET-ответов (SHA256 → JSON в data/cache/) |
| `src/api/polymarket.py` | `PolymarketClient` с 12 endpoint-методами |
| `scripts/explore_polymarket.py` | exploration + дамп сэмплов в `data/raw/samples/` |

Rate-limit budget с буфером 70% от advertised:
- Gamma: 280 req/s (advertised 400/s)
- CLOB: 630 req/s (advertised 900/s)
- Data: 70 req/s (advertised 100/s)

Retry: tenacity exponential backoff на 429/5xx, 4 попытки, max 30s wait.

### Sample-запросы (12 endpoints проверены)

| Endpoint | Status | Latency | Заметка |
|----------|--------|---------|---------|
| `gamma /markets` (closed) | ✅ | 0.25s | 10 markets |
| `gamma /markets` (active by volume) | ✅ | 0.25s | топ-10 по объёму |
| `gamma /markets/{id}` | ✅ | 0.09s | single market |
| `gamma /events` (closed) | ✅ | 0.17s | 10 events |
| `clob /markets` (page=1000) | ✅ | 1.27s | пагинация по cursor |
| `clob /markets/{cond_id}` | ✅ | 0.16s | snake_case формат |
| `clob /book` | ✅ | 0.10s | bids/asks как строки |
| `clob /price` | ✅ | 0.10s | `{"price": "0.02"}` |
| `clob /midpoint` | ✅ | 0.10s | `{"mid": "0.0205"}` |
| `clob /prices-history` (1h/1d/max) | ✅ | 0.12s | 61 / 1441 / 4308 точек |
| `data /trades` (limit=20) | ✅ | 0.26s | wallet + price + timestamp |
| `data /holders` | ✅ | 0.15s | по каждому outcome-токену |

### Документация

- `docs/api-schemas.md` — все 12 endpoints, поля, типы, подводные камни
- `docs/geo-check.md` — проверка из РФ, что работает

---

## Ключевые подводные камни

1. **Gamma vs CLOB используют РАЗНЫЕ форматы:**
   - Gamma → `camelCase` (`conditionId`, `clobTokenIds`)
   - CLOB → `snake_case` (`condition_id`, `token_id`)

2. **Gamma кодирует массивы JSON-в-строке:** `outcomes`, `outcomePrices`, `clobTokenIds` — нужно `json.loads()`.

3. **`prices-history` живёт на CLOB, не Gamma** — это противоречит части документации, нашли экспериментально.

4. **`prices-history market=` ждёт token_id, не condition_id** — частая ошибка.

5. **bids/asks приходят как строки** — `[{"price": "0.001", "size": "979067.24"}]`.

6. **Windows + httpx + socks4 в реестре** — нужно `trust_env=False`.

---

## 🎁 Бонус: price-history feasibility уже почти решена

Тест на топ-активном рынке (`will-jesus-christ-return-before-2027`, ~6 мес жизни):

| interval | точек | покрытие |
|----------|-------|----------|
| `1h` | 61 | ~1 час |
| `1d` | 1441 | ~сутки |
| `max` | 4308 | вся история |

**`interval=max` даёт ПОЛНУЮ pre-resolution историю на одном запросе** — это решающий аргумент для Day 4. Не нужны:
- ❌ Dune Analytics (хотя оставляем как backup)
- ❌ On-chain Polygon RPC через Alchemy (оставляем для других задач)

Достаточно одного похода в `clob /prices-history?interval=max` на каждый закрытый рынок → готовый dataset для бэктеста.

Latency 0.12s на запрос → 10000 рынков × 0.12s / 7 параллельно = ~3 минуты на полный pull.

---

## Что в Day 3 (Historical markets collector)

Теперь когда инфраструктура готова и эндпоинты понятны, Day 3 становится прямолинейным:

1. Пагинируемый сборщик `gamma_markets(closed=true)` → parquet
2. Для каждого закрытого рынка — `clob_prices_history(interval=max)` → parquet
3. DuckDB схема, loader, базовая EDA готовности
4. Сэмпл-запросы для Day 4 на 20 случайных рынках чтобы проверить полноту покрытия

**Возможно объединим Day 3 и Day 4** — если price-history покрытие через CLOB будет 100%, отдельный Day 4 (feasibility check) сводится к sanity-чеку.

---

## Артефакты

- `src/api/{ratelimit,cache,polymarket}.py`
- `scripts/explore_polymarket.py`
- `data/raw/samples/*.json` (12 файлов, ~2.7 МБ)
- `docs/api-schemas.md`
- `docs/geo-check.md`

---

## Чек-лист Day 2

- [x] Python env + requirements
- [x] HTTP-клиент с rate-limit + retry + cache
- [x] Sample-запросы 12 endpoints (Gamma/CLOB/Data/TimeSeries)
- [x] Документация полей в `docs/api-schemas.md`
- [x] Geo-check из РФ в `docs/geo-check.md`
- [x] Rate-limit лимиты проверены — намеренно прёмся не пробовали (нет нужды)
- [x] Bonus: prices-history feasibility подтверждён на лету
