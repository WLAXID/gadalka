# 📋 Polymarket API — схемы и заметки

> Day 2 Фазы 0. Документация полей и поведения трёх API,
> зафиксированная на сырых ответах из `data/raw/samples/`.

---

## TL;DR — что важно помнить

1. **Gamma vs CLOB** — РАЗНЫЕ форматы:
   - **Gamma** — `camelCase` (`conditionId`, `clobTokenIds`, `endDate`)
   - **CLOB** — `snake_case` (`condition_id`, `token_id`, `end_date_iso`)
2. **Gamma часто кодирует JSON-массивы в строки** — `outcomes`, `outcomePrices`, `clobTokenIds` приходят как `'["Yes", "No"]'`, нужно `json.loads`
3. **Identifiers:**
   - `id` (Gamma) — внутренний int как строка (`"703258"`)
   - `condition_id` / `conditionId` — `0x` + 64 hex (66 chars) — главный ключ рынка
   - `token_id` / `asset_id` / clobTokenIds — большой uint256 как строка
4. **`prices-history` живёт на CLOB**, не Gamma (`clob.polymarket.com/prices-history`)
5. **`interval=max`** даёт полную историю — для тестового рынка 6 мес = 4308 точек

---

## Gamma API (`https://gamma-api.polymarket.com`)

### `GET /markets`

Параметры: `closed`, `active`, `archived`, `limit`, `offset`, `order` (`endDate`, `volumeNum`, ...), `ascending`, `tag_id`, `condition_ids`.

Возвращает: `list[Market]`.

#### Структура `Market`

| Поле | Тип | Заметка |
|------|-----|---------|
| `id` | str (int) | внутренний ID Polymarket |
| `question` | str | формулировка рынка |
| `conditionId` | str (0x+64hex) | главный ключ; стыкуется с CLOB и Data |
| `slug` | str | url-friendly идентификатор |
| `description` | str | описание правил резолва |
| `endDate` | str (ISO) | дата ожидаемого резолва |
| `startDate` | str (ISO) | старт торговли |
| `closedTime` | str (ISO) | заполнено если `closed=true` |
| `outcomes` | **str** (JSON) | `'["Yes", "No"]'` — парсить через `json.loads` |
| `outcomePrices` | **str** (JSON) | `'["0.0205", "0.9795"]'` — последние цены |
| `clobTokenIds` | **str** (JSON) | `'["123...", "456..."]'` — token_id для CLOB |
| `volume` | str (float) | total volume USD |
| `volumeNum` | float | то же, число |
| `liquidity` | str (float) | текущая ликвидность |
| `active` | bool | торгуется ли |
| `closed` | bool | резолвлен ли |
| `archived` | bool | архивирован |
| `restricted` | bool | гео-ограничен (US-blocked обычно) |
| `enableOrderBook` | bool | есть orderbook (CLOB) |
| `orderPriceMinTickSize` | float | минимальный шаг цены (0.001 обычно) |
| `category` | str | `Politics`, `Sports`, `Crypto`, `Science`, `Culture`, ... |
| `tags` | list | теги (parent-derivative, и т.д.) |
| `resolvedBy` | str | адрес UMA оракула |
| `umaEndDate` | str (ISO) | дедлайн оракула |
| `questionID` | str | UMA вопрос ID |

> **Подводный камень:** `outcomes` и `outcomePrices` — массивы в строках. **Всегда** распаковывать через `json.loads`.

### `GET /markets/{id}`

Тот же `Market`-объект, по внутреннему `id`. Удобно для глубокой инспекции одного рынка.

### `GET /events`

События объединяют несколько markets. Например NBA-вечер с 10 матчами = 1 event + 10 markets.

| Поле | Тип | Заметка |
|------|-----|---------|
| `id` | str (int) | ID события |
| `ticker` | str | основной слаг |
| `slug` | str | дублирует ticker обычно |
| `title` | str | заголовок |
| `category` | str | категория |
| `volume` | float | суммарный объём |
| `openInterest` | int | OI |
| `volume24hr`, `volume1wk`, `volume1mo`, `volume1yr` | int | агрегации |
| `markets` | list[Market] | вложенные рынки |
| `competitive` | float | метрика конкуренции цен |
| `liquidity` | float | суммарная ликвидность |
| `endDate` | str (ISO) | end_date |

---

## CLOB API (`https://clob.polymarket.com`)

### `GET /markets`

Возвращает `{count, limit, data: list[Market], next_cursor}`. Пагинация по cursor (base64).

### `GET /markets/{condition_id}`

| Поле | Тип | Заметка |
|------|-----|---------|
| `condition_id` | str | главный ключ |
| `question_id` | str | UMA |
| `question` | str | |
| `description` | str | |
| `market_slug` | str | |
| `end_date_iso` | str (ISO) | |
| `enable_order_book` | bool | |
| `active` | bool | |
| `closed` | bool | |
| `archived` | bool | |
| `accepting_orders` | bool | |
| `accepting_order_timestamp` | str (ISO) | |
| `minimum_order_size` | int | min позиции в USD |
| `minimum_tick_size` | float | шаг цены |
| `maker_base_fee` | int | bps |
| `taker_base_fee` | int | bps |
| `neg_risk` | bool | мульти-outcome (>2) negative-risk market |
| `neg_risk_market_id` | str | если есть |
| `fpmm` | str | адрес legacy AMM (пусто для CLOB-only) |
| `tokens` | list | `[{token_id, outcome, price, winner}]` |
| `tags` | list[str] | |
| `rewards` | dict | rates, maker rebate структура |
| `is_50_50_outcome` | bool | |

### `GET /book?token_id=...`

Текущий orderbook одного outcome-токена.

| Поле | Тип | Заметка |
|------|-----|---------|
| `market` | str (condition_id) | |
| `asset_id` | str (token_id) | |
| `timestamp` | str (unix ms) | |
| `hash` | str | для idempotency |
| `bids` | list | `[{price: str, size: str}]` |
| `asks` | list | то же |
| `min_order_size` | str | |
| `tick_size` | str | |
| `neg_risk` | bool | |
| `last_trade_price` | str | |

> **Подводный камень:** `price` и `size` в bids/asks приходят как **строки**, не числа. Парсить через `float()`.

### `GET /price?token_id=...&side=buy|sell`

Минимальный ответ: `{"price": "0.02"}`.

### `GET /midpoint?token_id=...`

Минимальный ответ: `{"mid": "0.0205"}`.

### `GET /prices-history?market=<token_id>&interval=<int>` ⭐

**Главный endpoint для бэктеста.** Историческая цена outcome-токена.

| Параметр | Значения | Заметка |
|----------|----------|---------|
| `market` | token_id (не condition_id!) | |
| `interval` | `1m`, `1h`, `6h`, `1d`, `max` | `max` = вся история |
| `startTs` | unix sec | опционально |
| `endTs` | unix sec | опционально |
| `fidelity` | int | опционально |

Ответ:
```json
{"history": [{"t": 1779543664, "p": 0.0205}, ...]}
```

**Размеры на тестовом рынке (`will-jesus-christ-return-before-2027`, ~6 мес жизни):**

| interval | точек | окно |
|----------|-------|------|
| `1h` | 61 | последние ~1 час |
| `1d` | 1441 | последние сутки |
| `max` | 4308 | вся история (~6 мес) |

> Для разных интервалов окно ПО УМОЛЧАНИЮ разное. Если нужно «вся история с минутной гранулярностью» — нужно бить запросами с явным `startTs`/`endTs`.

### `GET /trades`

Последние сделки CLOB.

---

## Data API (`https://data-api.polymarket.com`)

### `GET /trades?market=<condition_id>&limit=N`

Историческая лента сделок. Каждая запись содержит данные кошелька, asset (token_id), outcome.

| Поле | Тип | Заметка |
|------|-----|---------|
| `proxyWallet` | str (addr) | кошелёк |
| `side` | str | `BUY` / `SELL` |
| `asset` | str (token_id) | какой outcome куплен |
| `conditionId` | str | condition_id рынка |
| `size` | float | количество USDC |
| `price` | float | цена |
| `timestamp` | int (unix sec) | |
| `outcome` | str | `"Yes"` / `"No"` |
| `outcomeIndex` | int | 0 / 1 |
| `transactionHash` | str (0x...) | tx на Polygon |
| `title` | str | заголовок рынка |
| `slug` | str | |
| `name`, `pseudonym`, `bio` | str | профиль пользователя (если есть) |

### `GET /holders?market=<condition_id>`

Возвращает `list[{token, holders}]` — по одной записи на каждый outcome-token.

`holders` — массив `{proxyWallet, balance, ...}` крупнейших держателей.

### `GET /positions?user=<addr>`

Позиции конкретного кошелька. Не тестировали в Day 2 (нет случайного wallet).

### `GET /value?user=<addr>`

Суммарный объём позиций кошелька.

---

## Identifiers — что во что преобразуется

```
Gamma /markets:
  id              → "703258"                          (внутренний)
  conditionId     → "0x0b4cc3b7..."  (64 hex, 66 chars total)
  clobTokenIds    → '["6932...", "5179..."]'  (JSON-string, два token_id для Yes/No)
  slug            → "will-jesus-christ-return-before-2027"

CLOB /markets/{condition_id}:
  condition_id    → "0x0b4cc3b7..." (тот же)
  tokens[].token_id → "6932..." / "5179..."
  market_slug     → тот же slug

Data /trades:
  conditionId     → "0x0b4cc3b7..."
  asset           → token_id ("6932...")
```

**Правила цепочки:**

- Хочешь metadata рынка → бери `condition_id` из Gamma, потом `clob_market(condition_id)` для деталей CLOB
- Хочешь orderbook / price → возьми `token_id` из Gamma `clobTokenIds` или из CLOB `tokens[].token_id`
- Хочешь historical price → нужен **token_id**, не condition_id
- Хочешь trades — оба варианта: `condition_id` для всего рынка, `asset` (token_id) для одного outcome

---

## Latency baselines (на active рынке, без кэша)

| Endpoint | Latency |
|----------|---------|
| `gamma /markets` (limit=10) | 0.15–0.25s |
| `gamma /markets/{id}` | 0.09–0.12s |
| `gamma /events` (limit=10) | 0.15–0.20s |
| `clob /markets` (page=1000) | 1.27s |
| `clob /markets/{cond_id}` | 0.16s |
| `clob /book` | 0.10s |
| `clob /price` | 0.10s |
| `clob /midpoint` | 0.10s |
| `clob /prices-history` (любой interval) | 0.12–0.16s |
| `data /trades` (limit=20) | 0.26–0.56s |
| `data /holders` | 0.15–0.41s |

prices-history НЕ зависит от объёма (4308 точек = те же 0.12s). Дешевый endpoint.

---

## Что НЕ протестировали в Day 2

- `data /positions` — нужен конкретный wallet
- `data /value` — то же
- `gamma /markets/search` — пагинация и фильтры
- CLOB write-endpoints — для Фазы 3+
- WebSocket — для Фазы 2+

---

## Артефакты Day 2

- `src/api/polymarket.py` — `PolymarketClient` (все 12 endpoints)
- `src/api/ratelimit.py` — token bucket per host
- `src/api/cache.py` — файловый кэш GET-ответов
- `scripts/explore_polymarket.py` — exploration с дампом sample'ов
- `data/raw/samples/*.json` — сырые ответы
