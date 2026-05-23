# 💎 Paid tier фичи провайдеров

> Что разблокируют платные планы у наших 30 провайдеров,
> и какие из этих фич полезны для gadalka.
> Обновлено: 2026-05-23

---

## Оглавление

- [TL;DR — что заюзать сразу](#tldr--что-заюзать-сразу)
- [Core providers (детально)](#core-providers-детально)
  - [1. Polygon.io / Massive](#1-polygonio--massive)
  - [2. Alchemy](#2-alchemy)
  - [3. Chainstack](#3-chainstack)
  - [4. NewsAPI](#4-newsapi)
  - [5. Tavily](#5-tavily)
  - [6. Perplexity](#6-perplexity)
  - [7. The Odds API](#7-the-odds-api)
- [Useful providers (кратко)](#useful-providers-кратко)
  - [8. OpenAI](#8-openai-tier-ladder)
  - [9. DeepSeek](#9-deepseek)
  - [10. CoinGecko](#10-coingecko)
  - [11. FRED](#11-fred-st-louis-fed)
  - [12. Bitquery](#12-bitquery)
  - [13. Finnhub](#13-finnhub)
- [Программное определение плана — рекомендации](#программное-определение-плана--рекомендации)
- [Probe-script — стоит ли писать](#probe-script--стоит-ли-писать)
- [Источники](#источники)

---

## TL;DR — что заюзать сразу

Из 30 провайдеров с пулом 785 ключей реально-полезные для gadalka платные планы:

### Топ-3 фичи, которые меняют возможности проекта

| # | Провайдер | Платная фича | Что это даёт gadalka |
|---|-----------|--------------|----------------------|
| 1 | **Alchemy Pay-as-You-Go** (или Free Tier!) | Full Archive Data + Debug API + Smart WebSockets | **Critical fallback для price-history**: восстановить кривую цены CTF Exchange по on-chain event logs до момента резолва. Работает даже на free-плане у Alchemy. |
| 2 | **NewsAPI Business ($449/mo)** | Historical archive до 5 лет + production-grade SLA | **Backtest news-lag стратегии**: без 5-летней истории бэктеста просто не выйдет — Developer-план даёт только 1 месяц с 24-час задержкой. Самый дорогой, но единственный, который реально открывает фичу. |
| 3 | **Polygon.io Stocks Starter ($29/mo)** | Unlimited API calls + 5y historical minute bars + WebSocket | **Crypto-категория Polymarket**: точный reference price BTC/ETH на момент резолва, минутная гранулярность для построения P(yes) features. Free даёт 5 RPM и EOD only — мало. |

### Что особенно ценно отметить

- **Alchemy на Free уже даёт archive + websocket + debug** — критичный для нас функционал доступен бесплатно (только rate-limit 25 RPS и 30M CU/mo). У нас 115 ключей — это огромный пул, который перекрывает любые лимиты.
- **Tavily $30/mo Researcher** разблокирует Extract / Crawl / Map API. Для news-aware фичи стоит как минимум один ключ.
- **Perplexity sonar модели** — единственный pay-per-use провайдер, где «платность» = просто платишь за вызовы. Online sonar-pro уже работает на любом ключе, лишь бы баланс был.

### Чего точно НЕ стоит апгрейдить

- **FRED** — целиком бесплатный, платных тарифов нет.
- **CoinGecko Demo** для нашего use-case достаточно: на крипту мы и так получаем данные через Polygon.io + on-chain. CoinGecko Analyst+ имеет смысл только если будем строить отдельный crypto-screener.
- **Finnhub Standard ($100/mo)** — для gadalka мало добавляет: фондовые данные есть в Polygon.io.
- **Bitquery Commercial** — Dune Analytics + Alchemy уже покрывают всё нужное по Polymarket on-chain.

---

## Core providers (детально)

### 1. Polygon.io / Massive

> ⚠️ Провайдер переименовался в **Massive** в начале 2026, домен `polygon.io` редиректит на `massive.com`. API endpoints остались прежние (`api.polygon.io`).

#### Тарифы (Stocks bundle)

| Tier | $/mo | Real-time | Hist depth | WS | RPM | Что разблокировано |
|------|------|-----------|------------|----|----|--------------------|
| Basic (free) | $0 | ❌ EOD only | 2 года | ❌ | 5 RPM | EOD aggregates, reference data |
| Starter | $29 | ❌ 15-min delayed | 5 лет | ✅ delayed | **unlimited** | Minute aggregates, technical indicators, fundamentals |
| Developer | $79 | ❌ 15-min delayed | 10 лет | ✅ delayed | unlimited | + snapshots, second aggregates |
| Advanced | $199 | ✅ real-time | 20+ лет | ✅ real-time | unlimited | Tick-level data, full historical depth |
| Currencies Starter | $49 | ✅ FX real-time | 10 лет | ✅ | unlimited | Forex tick + minute aggregates |
| Crypto Starter | $49 | ✅ real-time | 5 лет | ✅ | unlimited | Crypto tick-level, multi-exchange |

#### Фичи для gadalka

**Use case 1 — Crypto-категория Polymarket (priority HIGH)**

Polymarket-рынки вроде «BTC reach $X by date» резолвятся по реальной цене BTC. Нам нужно:
- Минутная гранулярность BTC/ETH (для построения features до резолва).
- Точное значение на момент резолва (источник truth).
- Real-time websocket — для in-play стратегий, опционально.

→ **Crypto Starter ($49/mo)** покрывает 100% потребностей. Real-time WS особенно ценен для last-mile рынков.

**Use case 2 — фондовые упоминания в новостях (priority LOW)**

Polymarket иногда делает рынки на «will SPY close above X». Реальный фондовый прайс с минутной гранулярностью. Starter $29 закрывает.

**Use case 3 — technical indicators (Phase 2+)**

Polygon отдаёт SMA/EMA/RSI/MACD как endpoints. Полезно для feature engineering на крипте, но можно посчитать локально — не критично.

#### Как определить план ключа

Polygon **не публикует** endpoint вроде `/v1/account/plan`. Определение — косвенное:

1. **HTTP 403 на real-time endpoint** → нет Advanced
   ```
   GET /v2/last/trade/X:BTCUSD
   ```
2. **Поле `delayed: true` в websocket auth response** → не Advanced
3. **HTTP 403 на минутные агрегаты до 5 лет назад** → Free tier (Basic)
4. **HTTP 429 после 5 RPM** → Free tier; Starter+ имеет unlimited
5. **Endpoint `/v3/reference/tickers` с большим limit (>50)** работает на Starter+

→ Probe-strategy: вызови `/v2/aggs/ticker/AAPL/range/1/minute/2020-01-01/2020-01-02` — если 200 OK, минимум Starter; если 403 — Basic.

---

### 2. Alchemy

> **Главный сюрприз**: на 2026 Alchemy убрал тарифы Growth/Scale и перешёл на схему **Free + Pay-as-You-Go + Enterprise**. **Archive data, Debug API и WebSockets теперь доступны на ВСЕХ тарифах включая Free.**

#### Тарифы

| Tier | $/mo | CU/mo | RPS | Apps | Webhooks | Archive | Debug | WS |
|------|------|-------|-----|------|----------|---------|-------|-----|
| **Free** | $0 | 30M | 25 | 5 | 5 | ✅ | ✅ | ✅ |
| **Pay-as-You-Go** | $0.45/M CU (первые 300M), потом $0.40/M | usage-based | 300+ | 30 | 100 | ✅ | ✅ | ✅ |
| **Enterprise** | custom | custom | 1000+ | 200 | 500 | ✅ | ✅ | ✅ |

**Trace API** — отдельно, доступен только для Enterprise (по словам pricing-страницы).

#### Фичи для gadalka

**Use case 1 — Archive event logs CTF Exchange (priority CRITICAL)**

Это **ключевой fallback** для построения price-history рынков Polymarket: если Dune queries не отдают нужный рынок, мы парсим on-chain event logs CTF Exchange и восстанавливаем кривую цены через trade events.

→ **Free tier уже достаточно**. У нас 115 ключей в пуле — это 115 × 30M = **3.45 миллиарда CU/месяц** только free-tier. Перекрытие на годы вперёд.

**Use case 2 — Smart WebSockets**

Подписка на новые блоки + конкретные адреса (CTF Exchange). Free tier поддерживает. Можем строить real-time stream новых trades.

**Use case 3 — Debug API**

`debug_traceTransaction` для разбора сложных транзакций. Free tier уже даёт. Полезно если будем разбираться, какие сделки повлияли на цену рынка.

#### Как определить план ключа

1. **GET dashboard JSON** (требует cookie session, не подходит для серверного определения).
2. **Через HTTP**: одинаковые endpoints на всех тарифах → косвенно через rate-limit:
   ```
   POST к eth_getLogs с большим fromBlock (archive) → 200 OK на всех планах
   429 после 25 RPS → Free
   429 после 300 RPS → PAYG
   ```
3. **Compute Units header**: Alchemy возвращает `X-Alchemy-CU-Used` в response — но это per-request, не cumulative.
4. **Через owner API** (если есть Access Token): `https://dashboard.alchemyapi.io/api/...` — но это не публичный API.

→ Probe-strategy: одно `eth_blockNumber` → если 200, ключ живой; rate-limit ловится только при нагрузке. Для gadalka это не критично — на Free всё работает.

---

### 3. Chainstack

#### Тарифы

| Tier | $/mo | RPS | Archive | WS | Debug/Trace | Dedicated |
|------|------|-----|---------|----|-----|-----------|
| Developer | $0 | 25 | ❌ | ✅ | ❌ | ❌ |
| Growth | $49 ($40 annual) | 250 | ✅ | ✅ | ✅ | ❌ |
| Business | $499 ($416 annual) | 600 | ✅ | ✅ | ✅ | ✅ |
| Enterprise | от $990 | unlimited | ✅ | ✅ | ✅ | unlimited |

**Биллинг**: 1 RU per request на shared nodes; archive nodes — 2 RU per request.

#### Фичи для gadalka

В отличие от Alchemy, **Chainstack Developer (free) НЕ имеет archive access**. Это значит:
- Если у нас в пуле 9 ключей Chainstack и среди них есть Growth+ — используем их **первыми** для archive queries.
- Если все 9 — Developer, то Chainstack бесполезен для нашего main use-case и работает только как fallback к Alchemy для свежих блоков.

→ Один **Growth-ключ Chainstack** даёт нам:
- 250 RPS archive (vs Alchemy 25 RPS на free).
- Debug/trace методы.
- Это апгрейд скорости, но не разблокировка фичи (Alchemy free уже даёт archive).

#### Как определить план ключа

1. **`eth_getLogs` с fromBlock=0** → 200 OK = archive (Growth+); 400/insufficient access = Developer.
2. **`debug_traceTransaction`** → 200 OK = Growth+; method not found = Developer.
3. **Rate-limit**: 25 RPS = Developer, 250 RPS = Growth, 600 RPS = Business.

→ Это **обязательная проверка** при загрузке пула — без неё не понять, какие ключи archive-capable.

---

### 4. NewsAPI

#### Тарифы

| Tier | $/mo | Requests | Hist depth | Live news | CORS | Commercial | Full content |
|------|------|----------|------------|-----------|------|------------|--------------|
| Developer | $0 | 100/day (~3k/mo) | 1 месяц | 24-час delay | localhost only | ❌ dev only | snippets |
| Business | $449 | 250k/mo + $0.0018 over | **5 лет** | real-time | all origins | ✅ | snippets |
| Advanced | $1749 | 2M/mo + $0.0009 over | 5 лет | real-time | all origins | ✅ | snippets |

⚠️ **Важно**: даже на платных планах NewsAPI возвращает только snippets, не полный текст статей. Полный текст — scraping через URL вручную.

#### Фичи для gadalka

**Use case 1 — Historical news для бэктеста news-lag (priority HIGH)**

News-lag стратегия: «новость X публикуется в T0, цена рынка Polymarket обновляется только в T0+15min — арбитраж в окне». Для бэктеста нужны исторические новости с timestamp. Developer план даёт **только 1 месяц** — на бэктесте за квартал уже не хватает.

→ **Business $449/mo** — единственный план, разблокирующий фичу. Это дорого, но альтернатив с историей за 5 лет немного (GDELT, но он сложнее).

Если в пуле 49 ключей NewsAPI попадёт хотя бы **один Business** — это меняет проект.

**Use case 2 — Real-time news pipeline (priority MEDIUM)**

Live news без 24-часового delay → можно делать live-features. Также Business+.

#### Как определить план ключа

NewsAPI **не имеет** account-endpoint. Определение — через probes:

1. **Запрос статей старше 1 месяца**: `GET /v2/everything?q=bitcoin&from=2024-01-01`
   - 200 OK с результатами → Business/Advanced
   - 426 Upgrade Required → Developer
2. **Запрос без `domains` фильтра** на большую выборку:
   - Developer ограничен `pageSize<=100` и `page<=5` (всего 500 max)
   - Business+ даёт `pageSize<=100, page<=1000` → проверяем доступ к page=10
3. **Заголовок origin** на CORS: с localhost — Developer; с любого origin — Business+.

→ Probe для NewsAPI критичен. Если найдём хотя бы 1 Business-ключ в пуле — это решает задачу бэктеста news-lag.

---

### 5. Tavily

#### Тарифы (2026)

| Tier | $/mo | Credits/mo | Search | Extract | Crawl/Map | Notes |
|------|------|------------|--------|---------|-----------|-------|
| Researcher (free) | $0 | 1000 | ✅ | ✅ basic | ❌ | Personal dev |
| Researcher paid | $30 ($25 annual) | ~4000 + PAYG | ✅ | ✅ advanced | ✅ | Single user |
| Startup | $100 ($83 annual) | ~15k + PAYG | ✅ | ✅ | ✅ | Team |
| Enterprise | custom | custom | ✅ | ✅ | ✅ + Map | SLA |
| Pay-as-you-go | — | $0.008/credit | — | — | — | Top-up |

**Стоимость операций в credits**:
- Search basic: 1 credit; advanced search: 2 credits.
- Extract basic: 1 credit / 5 URLs; advanced extract: 2 credits / 5 URLs.
- Map: 2 credits / 10 pages.
- Crawl = Map + Extract.

#### Фичи для gadalka

**Use case 1 — Extract API для парсинга статей (priority HIGH)**

Когда NewsAPI отдаёт snippet + URL, Tavily Extract достаёт полный текст статьи (рендерит JS, парсит контент). Это **то, без чего news-pipeline не работает**.

→ Free tier даёт basic Extract; для качества — Researcher $30/mo. У нас 8 ключей — даже на free это 8000 credits/mo, должно хватить на pre-Phase-1.

**Use case 2 — Advanced search depth (priority MEDIUM)**

Для feature «обзор последних новостей по событию» — advanced search возвращает более релевантные результаты.

**Use case 3 — Crawl API для специфичных доменов**

Если будем мониторить blog какого-то аналитика → Crawl. Низкий приоритет.

#### Как определить план ключа

Tavily **имеет** официальный usage-endpoint:

```bash
GET https://api.tavily.com/usage
Authorization: Bearer <api_key>
```

Возвращает:
- Текущее использование credits.
- Лимит plan.
- Дату reset.

→ **Это самый цивилизованный API для probe**. Делаем один вызов и сразу понимаем план + остаток.

---

### 6. Perplexity

#### Тарифы (по моделям, pay-per-use)

| Model | Input $/1M | Output $/1M | Search $/1k req | Notes |
|-------|------------|-------------|------------------|-------|
| sonar | $1 | $1 | $5–12 (low/med/high context) | Базовая online модель |
| sonar-pro | $3 | $15 | $6–14 | Продвинутая, больше context |
| sonar-reasoning-pro | $2 | $8 | $6–14 | Reasoning + online |
| sonar-deep-research | $2 | $8 | $5/1k searches + $2/1M citations | Long-form research |

⚠️ Все модели pay-per-use, нет «планов» как таковых. Tier определяется лимитами биллинга, а не подпиской.

#### Фичи для gadalka

**Use case 1 — sonar-online models (priority HIGH)**

Все sonar модели по умолчанию online (живой web search). Это значит запрос «what happened with [event] today» возвращает свежие данные с цитатами. **Один Perplexity-ключ заменяет связку «search engine + LLM summarization»**.

→ Используем для:
- Quick news lookup по конкретному Polymarket-событию.
- Feature «P(yes) по мнению LLM с цитатами».
- Sanity-check фактов в новостях.

**Use case 2 — citations API**

Все модели возвращают citations (URLs источников). Это критично для:
- Аудита: можем проверить, не галлюцинировал ли LLM.
- News-pipeline: цитаты → новые URLs для extract.

#### Как определить план ключа

Perplexity = pay-per-use, нет «плана». Но можно проверить:

1. **GET balance**: API endpoint `/api/balance` (если документирован — uncertain).
2. **POST `/chat/completions`** с моделью `sonar-pro` — если 200, ключ валидный и баланс > 0.
3. **Rate-limit headers** в response: `X-Ratelimit-Limit`, `X-Ratelimit-Remaining`.

→ Probe: один минимальный chat completion → если 200 = живой ключ. Дешевле всего пинговать `sonar` с 1-токен промптом.

---

### 7. The Odds API

#### Тарифы

| Tier | $/mo | Credits/mo | Historical | Live | Sports | Bookmakers |
|------|------|------------|------------|------|--------|------------|
| Free | $0 | 500 | ✅ | ✅ | all 70+ | all 40+ |
| 20K | $30 | 20 000 | ✅ | ✅ | all | all |
| 100K | $59 | 100 000 | ✅ | ✅ | all | all |
| 5M | $119 | 5 000 000 | ✅ | ✅ | all | all |
| 15M | $249 | 15 000 000 | ✅ | ✅ | all | all |

**Принципиально важно**: **все тарифы дают одинаковый функционал**, отличие только в quotas. Historical odds доступны на ВСЕХ планах, включая free.

#### Фичи для gadalka

**Use case 1 — Pinnacle line vs Polymarket sport (priority HIGH)**

Pinnacle закладывает edge ~2%, Polymarket — может быть ~5–10% на ликвидных рынках. Cross-platform арбитраж: Pinnacle → ground truth, Polymarket → mispricing.

→ Free 500 calls/mo — мало для прода. Но у нас **18 ключей в пуле = 9000 calls/mo на free** — этого достаточно для Phase 0/1 на одном рынке (NFL/EPL).

**Use case 2 — Historical odds для бэктеста (priority MEDIUM)**

Доступны на любом плане. Можем строить «как Pinnacle двигалась vs как Polymarket двигалась» исторически.

#### Как определить план ключа

The Odds API возвращает в каждом response headers:
- `X-Requests-Remaining` — сколько вызовов осталось в текущем биллинговом периоде.
- `X-Requests-Used` — сколько использовано.

→ Probe: один `/sports` запрос → headers покажут лимит, по нему вычислим план:
- ≤500 remaining = Free.
- 20k = $30.
- 100k = $59.
- И т.д.

**Это очень удобно** — план определяется без догадок.

---

## Useful providers (кратко)

### 8. OpenAI tier ladder

OpenAI делит аккаунты на **6 tiers** по cumulative spend (не monthly!):

| Tier | Cumulative spend | Account age | GPT-4o RPM/TPM | GPT-4o-mini RPM/TPM |
|------|------------------|-------------|----------------|---------------------|
| Free | $0 | — | очень низкие | очень низкие |
| Tier 1 | $5 | — | 500 / 30k | 500 / 200k |
| Tier 2 | $50 | 7+ дней | rising | rising |
| Tier 3 | $100 | 7+ дней | rising | rising |
| Tier 4 | $250 | 14+ дней | high | high |
| Tier 5 | $1000 | 30+ дней | **10000 / 800k** | **10000 / 4M** |

#### Что важно для gadalka

- На любом tier доступны все модели (включая o1, o3). Tier влияет только на лимиты.
- **Batch API** работает в отдельном пуле + 50% скидка → если у нас 12 ключей, можно через batch шерстить большие объёмы новостей.
- Между ключами в пуле может быть огромный разброс tier-ов. Tier 5 ключ ≈ 20× throughput Tier 1.

#### Программное определение tier

OpenAI **не имеет** официального endpoint вроде `/v1/account/tier`. Но:
1. Response headers содержат `x-ratelimit-limit-requests` и `x-ratelimit-limit-tokens` → по ним вычисляем tier.
2. Endpoint `GET /v1/dashboard/billing/subscription` доступен с user-session token (не API key) → не подходит для probe.

→ Probe: один `chat.completions` с моделью `gpt-4o`, читаем `x-ratelimit-limit-requests` из response headers. Сравниваем с таблицей выше.

---

### 9. DeepSeek

#### Pricing (по моделям, pay-per-use)

| Model | Input $/1M (cache miss) | Output $/1M | Context | Concurrent |
|-------|--------------------------|--------------|---------|------------|
| DeepSeek-V4-Flash | $0.14 | $0.28 | 1M tokens | 2500 |
| DeepSeek-V4-Pro | $0.435 | $0.87 | 1M tokens | 500 |

**Cache hit ~ 1/50 от cache miss** → если кешируем стабильный system-prompt, цена падает почти на два порядка.

⚠️ До 31 мая 2026 действует **скидка 75% на Pro** — стоит закупиться.

#### Для gadalka

DeepSeek = дешёвая альтернатива OpenAI для bulk-обработки новостей. 4 ключа = пул для batch news-summarization.

#### Определение «плана»

Нет планов как таковых. Просто проверяем баланс через `/v1/user/balance` (документированный endpoint DeepSeek).

---

### 10. CoinGecko

#### Тарифы

| Tier | $/mo | Calls/mo | Calls/min | Hist | Endpoints | WS |
|------|------|----------|-----------|------|-----------|-----|
| Demo (free) | $0 | 10k | 100 | 1 год | basic | ❌ |
| Basic | $35 ($29 annual) | 100k | 300 | 2 года | 50+ | ❌ |
| Analyst | $129 ($103 annual) | 500k | 500 | 10 лет | 70+ | ✅ |
| Lite | $499 ($399 annual) | 2M | 500 | 10 лет | 70+ | ✅ WS + Webhook |
| Enterprise | custom | custom | custom | 10+ лет | 80+ | ✅ SLA |

#### Для gadalka

Минимально полезен. CoinGecko может быть **fallback** для крипто-прайсов, если Polygon.io упадёт, но у нас уже 67 ключей CoinGecko + 40 Polygon → redundancy огромная.

**Analyst** имел бы смысл, если бы делали independent crypto-screener. Для gadalka — нет.

#### Определение плана

CoinGecko различает headers:
- `x-cg-demo-api-key` → Demo plan, endpoint base `api.coingecko.com`.
- `x-cg-pro-api-key` → Pro plans (Basic/Analyst/Lite), endpoint base `pro-api.coingecko.com`.

→ Probe: `GET /ping` с pro-header.
- 200 OK с `x-cg-pro-api-key` → платный.
- 401 с pro-header → ключ не Pro.
- Затем `GET /api/v3/exchanges` — endpoint доступный только на Analyst+ → различаем Basic от Analyst.

---

### 11. FRED (St. Louis Fed)

**Полностью бесплатный**. Платных планов нет. Free API key, rate-limit 120 req/min.

→ FRED — это золотая середина: 26 ключей в пуле гарантируют, что мы никогда не упрёмся в лимит. Все макроэкономические серии (CPI, FED rate, unemployment) — для feature engineering.

Probe: `GET /fred/series?series_id=GDP&api_key=...&file_type=json` → если 200, ключ живой. Других tier-ов нет.

---

### 12. Bitquery

#### Тарифы

| Tier | $/mo | Points | RPS | Streams |
|------|------|--------|-----|---------|
| Developer (free) | $0 | 1000 trial | 10 RPM | 2 |
| Commercial | contact sales | unlimited | high | high |

#### Для gadalka

Не нужен. Bitquery — общая multichain GraphQL платформа, но Dune Analytics для Polymarket даёт нам всё специфичное. 14 free-ключей лежат как страховой запас.

Probe: GraphQL `{ __schema { types { name } } }` → если 200, ключ валидный.

---

### 13. Finnhub

#### Тарифы

| Tier | $/mo | RPM | News | Fundamentals | WS | Commercial |
|------|------|-----|------|---------------|-----|------------|
| Free | $0 | 60 | ✅ basic | limited | ✅ basic | ❌ |
| Standard | $100 | high | ✅ premium | full | ✅ | ✅ |
| Premium | $600 | very high | ✅ + analyst | full + estimates | ✅ | ✅ |

#### Для gadalka

Минимально полезен. Stocks-данные есть в Polygon. Может пригодиться для **earnings calendar** и **insider transactions** — но это уже отдельный feature space.

→ Apgrade не стоит. Использовать free для backup news + earnings dates.

Probe: `/quote?symbol=AAPL` → ловим `x-ratelimit-limit` в headers.

---

## Программное определение плана — рекомендации

Сводная таблица, как для каждого провайдера определить план **через API**:

| Провайдер | Метод | Endpoint / Header |
|-----------|-------|-------------------|
| **Polygon.io** | Косвенно (probe + 403) | `/v2/aggs/.../minute/2020-01-01/...` |
| **Alchemy** | Rate-limit probe | `eth_blockNumber` + 429 после X RPS |
| **Chainstack** | Method probe | `debug_traceTransaction` → 200/method-not-found |
| **NewsAPI** | Date probe | `/v2/everything?from=2024-01-01` → 426 или 200 |
| **Tavily** | **Официальный** | `GET /usage` ✅ |
| **Perplexity** | Balance probe | `POST /chat/completions` minimal + headers |
| **The Odds API** | **Headers** | `X-Requests-Remaining` после любого запроса ✅ |
| **OpenAI** | Headers | `x-ratelimit-limit-requests` после chat call |
| **DeepSeek** | **Официальный** | `GET /v1/user/balance` ✅ |
| **CoinGecko** | Headers + endpoint probe | `x-cg-pro-api-key` + `/exchanges` для tier |
| **FRED** | Не нужно | Один tier только |
| **Bitquery** | Schema probe | GraphQL introspection |
| **Finnhub** | Headers | `x-ratelimit-limit` |

### Категории провайдеров

- ✅ **Цивилизованный probe**: Tavily, DeepSeek, The Odds API — официальные endpoint'ы.
- ⚠️ **Header probe**: OpenAI, Finnhub, Perplexity — один минимальный запрос → читаем headers.
- 🟡 **Endpoint probe**: Chainstack, NewsAPI, CoinGecko, Polygon — нужен probe-запрос к специфичной фиче, по коду ответа понимаем доступ.
- 🔴 **Косвенный**: Alchemy (через rate-limit при нагрузке).

---

## Probe-script — стоит ли писать

### Рекомендация: **ДА**, написать `scripts/probe_keys.py`

Аргументы:
1. У нас 785 ключей, ручная проверка — не вариант.
2. Для **NewsAPI, Chainstack, Polygon** определение плана = **прямая разблокировка фич** (если найдём 1 Business NewsAPI, бэктест news-lag станет возможен).
3. После Phase 0 пул будет жить долго → probe должен быть автоматическим (обновлять метаданные ключа в keypool).

### Архитектура `scripts/probe_keys.py`

```python
# Псевдокод
for provider in PROVIDERS:
    for key in keypool.get(provider):
        try:
            result = PROBES[provider](key)
            # result = {'plan': 'Business', 'rate_limit': 250000, 'detected_via': 'date_probe'}
            keypool.update_meta(provider, key, result)
        except Exception as e:
            keypool.mark_dead(provider, key, reason=str(e))
```

### Probes по провайдерам (минимальный набор)

| Провайдер | Probe call | Парсинг |
|-----------|------------|---------|
| Polygon.io | `GET /v2/aggs/ticker/AAPL/range/1/minute/2020-01-01/2020-01-02` | 200 → Starter+; 403 → Basic |
| Alchemy | `POST {"method":"eth_blockNumber"}` | 200 → live; cookie-based plan detect = skip |
| Chainstack | `POST {"method":"debug_traceTransaction"}` | 200 → Growth+; error → Developer |
| NewsAPI | `GET /v2/everything?q=test&from=2024-01-01&pageSize=1` | 200 → Business+; 426 → Developer |
| Tavily | `GET /usage` | JSON `plan` + `credits_remaining` |
| Perplexity | `POST /chat/completions` minimal | headers `x-ratelimit-*` |
| The Odds API | `GET /v4/sports` | headers `X-Requests-Remaining` |
| OpenAI | `POST /v1/chat/completions` minimal | headers `x-ratelimit-limit-requests` |
| DeepSeek | `GET /v1/user/balance` | JSON balance |
| CoinGecko | `GET /ping` с pro-header; `/exchanges` для tier-probe | header behavior + 200/401 |
| Bitquery | `POST` minimal GraphQL query | 200 → live |
| Finnhub | `GET /quote?symbol=AAPL` | headers `x-ratelimit-limit` |

### Что складывать в `keypool.meta`

```json
{
  "provider": "newsapi",
  "key_hash": "...",
  "plan": "Business",
  "rate_limit_mo": 250000,
  "features": ["historical_5y", "live_news", "cors_all"],
  "probed_at": "2026-05-23T18:00:00Z",
  "valid": true
}
```

→ потом router выбирает ключ под нужную фичу (например, для бэктеста news-lag берёт только ключи с `historical_5y`).

---

## Источники

- [Polygon.io pricing (redirects to Massive)](https://polygon.io/pricing)
- [Alchemy pricing](https://www.alchemy.com/pricing)
- [Chainstack pricing](https://chainstack.com/pricing/)
- [NewsAPI pricing](https://newsapi.org/pricing)
- [Tavily pricing docs](https://docs.tavily.com/documentation/api-credits)
- [Tavily Usage Endpoint announcement](https://community.tavily.com/t/usage-endpoint-now-live/863)
- [Perplexity pricing](https://docs.perplexity.ai/guides/pricing)
- [The Odds API pricing](https://the-odds-api.com/#get-access)
- [CoinGecko pricing](https://www.coingecko.com/en/api/pricing)
- [CoinGecko ping endpoint](https://docs.coingecko.com/reference/ping-server)
- [Finnhub pricing](https://finnhub.io/pricing)
- [Bitquery pricing](https://bitquery.io/pricing)
- [OpenAI usage tiers (Inference.net guide)](https://inference.net/content/openai-rate-limits-guide/)
- [DeepSeek pricing](https://api-docs.deepseek.com/quick_start/pricing)
- [FRED API docs](https://fred.stlouisfed.org/docs/api/fred/)
