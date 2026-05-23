# 📡 API Landscape для gadalka

> Каталог API, которые могут пригодиться. Day 1 Фазы 0 (Discovery).
> Дата составления: 2026-05-23. Геолокация пользователя: РФ.
> Каждый API оценён по релевантности 1-5:
> - **5** = критично для Фазы 0/1
> - **4** = обязательно в Фазе 2/3
> - **3** = полезно, но опционально
> - **2** = в бэклог на потом
> - **1** = не нужно / не подходит

---

## Оглавление

1. [Polymarket собственные API](#1-polymarket-собственные-api)
2. [Конкуренты-маркеты (cross-platform арбитраж)](#2-конкуренты-маркеты-cross-platform-арбитраж)
3. [On-chain RPC для Polygon](#3-on-chain-rpc-для-polygon)
4. [On-chain indexers и аналитика](#4-on-chain-indexers-и-аналитика)
5. [Новости и social](#5-новости-и-social)
6. [LLM API](#6-llm-api)
7. [Сравнительные котировки спорт-букмекеров](#7-сравнительные-котировки-спорт-букмекеров)
8. [Сводка: что подключаем в Фазе 0/1](#сводка-что-подключаем-в-фазе-01)
9. [Источники](#источники)

---

## 1. Polymarket собственные API

Polymarket — основной объект исследования. Все четыре их публичных API доступны без аутентификации для read-only; write (отправка ордеров) требует подписи EOA-кошелька с депозитом USDC на Polygon. Для РФ read доступен, write — формально разрешён (в отличие от US-residents).

| API | Base URL | Free tier | Auth | Лимиты | Релевантность | Заметки |
| --- | --- | --- | --- | --- | --- | --- |
| **Gamma (metadata)** | `https://gamma-api.polymarket.com` | Да, бесплатно без ключа | Не нужна для read | 4000/10s общий; `/events` 500/10s; `/markets` 300/10s; `/search` 350/10s | **5** | Базовый источник о событиях, рынках, тегах, slug. Cloudflare-throttling вместо 429. |
| **CLOB (orderbook + trade)** | `https://clob.polymarket.com` | Да, read без ключа | L1+L2 подпись для write | 9000/10s общий; `/book`, `/price`, `/midprice` 1500/10s; batch 500/10s; POST `/order` 3500/10s burst / 36000/10min sustained (~60/s avg); DELETE `/order` 3000/10s burst / 30000/10min | **5** | Orderbook, котировки, маркет-меикинг. Real-time лучше брать через WS (`wss://ws-subscriptions-clob.polymarket.com/ws/`). |
| **Data API** | `https://data-api.polymarket.com` | Да | Не нужна для read | 1000/10s общий; `/trades` 200/10s; `/positions` 150/10s | **5** | Trade history, leaderboard, holdings отдельных адресов. Полезно для пост-фактум анализа стратегий китов. |
| **TimeSeries (prices-history)** | `https://clob.polymarket.com/prices-history` | Да | Не нужна | Делит лимит CLOB (1500/10s для single endpoints) | **5** | GET с `market` (clobTokenId), `interval` (1m, 1h, 6h, 1d, max), `startTs`/`endTs`. Используется для бэктеста — это критично. |
| **Sports WebSocket** | `wss://ws-live-data.polymarket.com/sports` | Да | Не нужна | n/a (стрим) | **3** | Real-time push для спорт-событий, slug формата `nfl-buf-kc-2025-01-26`. Sub-100ms latency. |

**Контракты на Polygon (chainId 137):**
- CTF Exchange (orderbook): `0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E`
- Neg Risk CTF Exchange: `0xC5d563A36AE78145C45a50134d48A1215220f80a`
- Conditional Token Framework: `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045`
- USDC (Polygon): `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174`

**Заметки по РФ:** read API доступен без VPN; write требует Polygon-wallet (без географической проверки на контракт-уровне). Sanctions / OFAC compliance проверяется только на уровне UI / KYC, не API.

---

## 2. Конкуренты-маркеты (cross-platform арбитраж)

Гипотеза: одно и то же событие (выборы, спорт, BTC > X) котируется по разной цене на Polymarket и других платформах → арб.

| Платформа | API base | Free tier | Auth | Лимиты | Гео | Релевантность | Заметки |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **Kalshi** | `https://api.elections.kalshi.com/trade-api/v2` | Да, бесплатно | RSA PKCS#8 2048-bit + JWT (24h) | Public ~30 req/s; auth ~10 req/s; token-cost model по тиру (Basic/Advanced/Premier/...) | **US-only** для трейдинга; read API технически доступен снаружи, но account нужен US-резидент | **4** | Главный политический конкурент после изгнания PredictIt. Для арба из РФ — read OK, write требует US-доку. Есть demo sandbox. |
| **PredictIt** | `https://www.predictit.org/api/marketdata/all/` | Да | Не нужна | Не задокументированы; ~5 req/s безопасно | US-only (CFTC no-action) | **2** | Только public price feed, нет trading API. Низкий cap $850/контракт. Скорее как референс-цена для cross-check, чем для арба. |
| **Manifold Markets** | `https://api.manifold.markets/v0` | Да, полностью бесплатно | Optional `Authorization` header с API-key | **500 req/min на IP** | Глобально | **3** | Play-money рынки (mana), но реал-токены через charity. Для backtest сигналов "что думает crowd" без денег — идеально. |
| **Polymarket-Sports (отдельный slug-набор)** | См. п.1 (тот же домен) | Да | Не нужна | Делит с CLOB | Глобально | **3** | Не отдельный API, а раздел Polymarket. MLB officially partnered (multiyear deal). NBA/NHL player props в roadmap. |
| **Augur (v2)** | Только on-chain (Ethereum) | Да | Wallet sig | RPC-bound | Глобально, но низкая ликвидность | **1** | Реально dead-протокол, low volume. Полезен только как исторический dataset. |
| **Hedgehog Markets** | `https://api.hedgehog.markets` | Да | Wallet (Solana) | Низкие | Глобально | **2** | Solana-based, растёт. На будущее для diversification. |
| **Myriad Markets** | Через FinFeedAPI / напрямую | Да | Не нужна для read | Не задокументированы | Глобально | **2** | Новый игрок, низкая ликвидность пока. |
| **Prediction Hunt (агрегатор)** | `https://api.predictionhunt.com/v2` | Да, free w/o card | `X-API-Key` | Тариф-зависимо | Глобально | **3** | Unified API для Kalshi+Polymarket+PredictIt+ProphetX+Opinion. Удобно для cross-platform matching, но dependency на third-party. |
| **FinFeedAPI** | `https://api.finfeedapi.com` | Тариф | API-key | Тариф-зависимо | Глобально | **2** | Unified для Polymarket+Kalshi+Myriad+Manifold. Платный после free trial. |

**Вывод по разделу:** для Фазы 1 (backtest cross-platform арба) Kalshi + Manifold — обязательные дополнения. Kalshi даст political markets с US-ликвидностью, Manifold — sentiment-proxy без денег.

---

## 3. On-chain RPC для Polygon

Polymarket весь on-chain на Polygon PoS. Для reconstruction price-history через events (Filled, OrderCancelled, etc.) или для проверки tx нужен надёжный RPC. Critically: **нужен archive node**, потому что pos-history глубже 128 блоков (state) → standard RPC возвращает "missing trie node".

| Провайдер | Free tier | Archive | Лимиты free | Платно от | Релевантность | Заметки |
| --- | --- | --- | --- | --- | --- | --- |
| **Alchemy** | Да, бессрочно | Да (на free) | 30M CU/мес ≈ 1.8M req; 330 CU/s burst | Growth $49/mo | **5** | Best-in-class DX, eth_getLogs work well. CU-модель — сложные методы (логи) кушают 75+ CU. Архив включён в free. |
| **Chainstack** | Да, бессрочно | Elastic Archive (от Growth $49) | 3M req/мес | Growth $49 + usage (20M RU); Business $349 | **4** | Хорошая альтернатива Alchemy. Unlimited Node add-on (фиксированный месячный тариф вместо per-request) — для production. |
| **Infura** | Да | Только paid | 100K req/день | Платно variable | **3** | Старый игрок, теперь MetaMask. На free — мало для backtest. |
| **QuickNode** | **Только 30-day trial** (10M credits) | Yes на paid | Нет постоянного free | Build $49/mo (80M credits, 50 RPS) | **2** | Без постоянного free — менее удобен для Фазы 0. |
| **Public polygon-rpc.com** | Да, anon | Нет | ~40-43 req/s, потом 429 | n/a | **2** | Хорошо для smoke-test, не для backtest или production. С 2026 требует sign-in для full speed. |
| **Ankr Public RPC** | Да | Limited | Низкие | Платно от $50 | **2** | Backup endpoint, нестабилен. |
| **Llamarpc / Drpc / GetBlock** | Да | Зависит | Низкие | Платно | **2** | Use as fallback в health-check rotation. |

**Рекомендация:** Alchemy (primary) + Chainstack (fallback) + public RPC (smoke-test). Для Фазы 1 (backtest на ~2 года) понадобится ~5-20M log-запросов → 30M CU free должно хватить, если кэшировать локально.

---

## 4. On-chain indexers и аналитика

Гипотеза: вместо тысяч RPC eth_getLogs можно тянуть pre-indexed данные пачками через SQL/GraphQL. Это критично для скорости backtest reconstruct.

| Сервис | API | Free tier | Auth | Polymarket-готовность | Релевантность | Заметки |
| --- | --- | --- | --- | --- | --- | --- |
| **Dune Analytics** | REST + SQL via API | Да, base free; credit-based | API-key | **Очень богатая** — десятки community queries (`dune.com/polymarket`, dunedata, rchen8, filarm, permary). Hourly OHLC + fills + positions за 4 года | **5** | Идеально для Фазы 0/1: можно тянуть готовые dataset'ы без своего ingestion. Free credit'ы ограничены, но достаточно для discovery. |
| **The Graph (decentralized)** | GraphQL per-subgraph | Pay-per-query (GRT) | API-key | Polymarket official subgraph (`polymarketmatic` namespace). Hosted Service **deprecated в 2026** | **4** | Через decentralized network. Стандарт индустрии для on-chain GraphQL. Дешевле чем self-host. |
| **Goldsky** | GraphQL/streams | Да, Starter free; $0.05/hr worker + $4/100k entities | API-key | Polymarket — **их клиент** для real-time data | **4** | Если нужны self-deployed subgraphs или Mirror pipelines. Best for low-latency. |
| **Bitquery** | GraphQL | Да, free credits | `X-API-KEY` | Готовые dashboards: PredictionTrades, PredictionSettlements, CTF Exchange API. Realtime ~7 дней | **3** | Хорошо для real-time, но retention слабый. Удобный GraphQL IDE. |
| **Flipside Crypto** | REST + Snowflake SQL | Да, free with quota | API-key | Есть Polygon-coverage, но не уверены про Polymarket dataset | **3** | Конкурент Dune. Curated tables, AI agents. Free tier есть, but enterprise-pivot. |
| **Subsquid** | GraphQL / SDK | Да, до Network | Open | Нужно деплоить свой squid | **2** | Если Dune/Goldsky не хватит. DIY-вариант. |
| **Allium** | REST/SQL | Enterprise-only | Sales contract | Coverage есть, нет публичного pricing | **1** | Для enterprise, нам overkill. |

**Вывод:** Dune — primary для Фазы 0 (discovery/EDA), Goldsky или The Graph — для Фазы 2+ (real-time pipeline).

---

## 5. Новости и social

Будущая фича: news → P(yes) prediction. Сигналы из новостей и social превращаются в фичи для модели.

| Источник | API | Free tier | Auth | Лимиты | Релевантность | Заметки |
| --- | --- | --- | --- | --- | --- | --- |
| **NewsAPI.org** | REST | Да, dev only (100 req/day, no commercial) | API-key | 100/day free | **2** | Только dev. Commercial от $449/mo. Стандартная reuters/AP агрегация. |
| **Tavily** | REST search/extract/crawl | Да, **1000 query/mo** free | API-key | 1000/mo | **4** | AI-optimized search с ranked snippets. Для LLM-агентов — самый удобный SOURCE. Платно от $8/1k. |
| **Perplexity Sonar API** | REST | Pay-as-you-go | API-key | Базовый Sonar: $1/M input, $1/M output (примерно). Sonar Pro: $3/M input, $15/M output. Search API: $5/1k req | **3** | Если нужен LLM-with-search в одном вызове. Удобно, но дублирует Claude+Tavily. |
| **Twitter / X API** | REST + filtered stream | **Free tier УБРАН в 2026**. Pay-per-use по умолчанию | OAuth 2.0 | $0.005/read (cap 2M reads/mo), $0.01/post create | **2** | Реально дорого для high-volume social monitoring. Legacy Basic $200/mo и Pro $5000/mo — только для существующих. Скрейпинг — risk. |
| **Reddit API** | REST | Да, ограниченный (60 req/min OAuth) | OAuth | Enterprise $0.24/1k calls | **3** | Хорошо для r/PredictionMarkets, r/politics, r/sportsbook sentiment. Free tier рабочий для low-volume. |
| **RSS-агрегаторы** (Feedly Cloud API, Inoreader) | REST | Тариф | API-key / OAuth | Тариф | **3** | Дёшево/бесплатно для известных feed'ов (Reuters, AP, Bloomberg). |
| **GDELT 2.0** | Files + GeoCoder + Doc API | **Полностью бесплатно** | Не нужна | Soft-rate (политесс ~ 1 req/s) | **5** | Глобальный news event stream, CAMEO-coded (актёр-действие-цель + tone). Уникален для geopolitical/election features. Идеально под цели проекта. |
| **EventRegistry** | REST | Trial 14d | API-key | От $600/mo | **2** | 300k+ источников, кластеризованные event-graphs. Дорого, но качественно. В бэклог. |
| **SearXNG (self-host)** | REST | Open-source | Не нужна | Self-host | **3** | Полезный fallback для агрегированного поиска без vendor lock-in. |
| **Newsdata.io** | REST | Да, 200 req/day | API-key | Платно от $149/mo | **2** | Альтернатива NewsAPI с более щедрым free. |

**Вывод:** GDELT + Tavily — основа для discovery; Reddit + RSS — добавим в Фазе 2.

---

## 6. LLM API

Для будущей фичи "новость → P(yes)" и для исследовательских агентов. Сравнение свежее на май 2026.

| Провайдер / Модель | Input / Output ($ per 1M tok) | Context | Latency | Релевантность | Заметки |
| --- | --- | --- | --- | --- | --- |
| **Anthropic Claude Opus 4.7** | $5 / $25 (с 1M context — без surcharge) | 1M tok | Медленнее всех (~5-15s TTFT) | **4** | Лучший для сложных рассуждений и LongContext. Новый tokenizer выдаёт до +35% токенов vs Opus 4.6. Prompt caching -90%. |
| **Anthropic Claude Sonnet 4.6** | $3 / $15 | 1M tok | ~2-5s | **5** | Sweet spot цена/качество. Будет дефолтом для большинства inference в проекте. Batch API -50%. |
| **Anthropic Claude Haiku 4.5** | $1 / $5 | 200k | <1s | **4** | Высокочастотные классификации (sentiment, novelty filter). |
| **OpenAI GPT-5.5** | $5 / $30 (cached input $0.50) | ~256k | ~3-7s | **3** | Хорош для structured output (JSON schema). Дороже Sonnet на output. |
| **OpenAI GPT-5.4** | $2.50 / $15 | ~256k | ~2-5s | **3** | Прямой конкурент Sonnet 4.6. |
| **OpenAI GPT-5.4 Nano** | $0.20 / $1.25 | 128k | <1s | **4** | Лучший $/токен для high-volume tasks. |
| **OpenAI GPT-4.1 Nano** | $0.10 / $0.40 | 128k | <1s | **3** | Самая дешёвая prod модель OpenAI. |
| **Google Gemini 3.1 Pro** | $2 / $12 | 2M tok | ~3-6s | **4** | Native multi-modal (видео ленты с дебатов?). 2M context = весь архив новостей за месяц в одном промпте. |
| **Google Gemini 3 Flash** | $0.50 / $3 | 1M tok | <1s | **5** | Бюджетный массовый inference. Уникальное соотношение context/цена. |
| **Google Gemini 2.5 Pro (legacy)** | $1.25 / $10 (до 200k), выше — surcharge | 2M tok | ~3-5s | **2** | Уйдёт в EOL, но пока есть. |
| **xAI Grok-4** | Тариф | ~256k | ~5s | **2** | Real-time Twitter-doc grounding — теоретически полезно для prediction-feed. |
| **DeepSeek V3.2 (через API)** | $0.27 / $1.10 примерно | 128k | ~3s | **3** | Дёшево, sparse-attention для long context. Geopolitics: китайский провайдер — учитывать для приватных данных. |
| **Ollama (self-host)** — Llama 3.3 70B | $0 + железо (~$0.5/час GPU) | 8k-128k (Modelfile) | Зависит от GPU; RTX 4090 ~30 tok/s | **3** | Полная приватность. Production-running неудобен (xda-developers критика), но dev — отлично. |
| **Ollama** — DeepSeek V3.2 / R1 | $0 + железо | 128k+ | На уровне RTX 4090: ~10-20 tok/s | **3** | Sparse attention эффективно для long context локально. |
| **Ollama** — Qwen 3, GLM-5, Kimi K2.5, gpt-oss | $0 + железо | Разный | Зависит | **3** | Огромный выбор open-source весов в 2026. |

**Вывод:** для production inference — Claude Sonnet 4.6 (primary), Haiku 4.5 (high-freq), Gemini 3 Flash (cheapest 1M context). Ollama — для dev/приватного эксперимента.

---

## 7. Сравнительные котировки спорт-букмекеров

Используем для арба Polymarket Sports ↔ традиционные букмекеры. Если на Polymarket NBA-moneyline стоит 0.42, а у Pinnacle implied 0.48 — это сигнал.

| Сервис | API | Free tier | Cost | Pinnacle | Betfair Exchange | Гео | Релевантность | Заметки |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **The Odds API** | REST | **500 req/mo free** | От $25/mo (20k req) | **Нет** | **Нет** | Глобально | **3** | Ширпотреб-букмекеры (Bet365, DraftKings, FanDuel, William Hill — ~40 шт). Bad для sharp-line арба. |
| **SportsGameOdds** | REST | Да + 7-day trial | $99 - $499/mo | **Да** | Нет | Глобально | **4** | Включает Pinnacle (sharp line), 80+ books. Лучший вариант если нужна Pinnacle reference. |
| **OddsJam** | REST | Нет публичного | ~$500-$1000+/mo | Косвенно (sharp-line proxy) | Косвенно | Глобально | **2** | Дорого. Очень богатые data: 100+ books, injury, props. |
| **Pinnacle (через third-party)** | Direct API устарел | Только Pinnacle Solutions для licensed | Negotiated | n/a (sharp source) | n/a | Не глобально | **3** | Sharp line — главный signal benchmark. Доступ только через резеллеров (SportsGameOdds, BetsAPI). |
| **Betfair Exchange API** | REST + Streaming | Delayed App Key free | **£499 единоразово** за Live App Key | n/a | n/a (это и есть Betfair) | **РФ — НЕ заблокирован формально**, но KYC + банк-карта могут потребовать вид на ж-во. UK/EU full | **4** | Exchange-orderbook — самый чистый prob-source для спорта. Из РФ technically возможно, but банковский rail сложен. Бэклог. |
| **OpticOdds** | REST + WS | Trial | Negotiated | Да | Да | Глобально | **3** | Premium. Покрывает Polymarket как "sportsbook" в своей feed (полезно). |
| **BetsAPI** | REST | Trial | $30-$200+/mo | Да | Да | Глобально | **3** | Бюджетный аналог OddsJam. |
| **RebelBetting** | UI-tool с экспортом | 14-day trial (cap 3.5% profit) | $99-$199/mo subscription | Через broker | Через broker | EU/UK focus | **2** | Не чистый API, скорее UI+alerts. Для исследования арб-стратегий полезен как референс. |

**Вывод:** для Фазы 1 (research только) хватит The Odds API free tier + SportsGameOdds free trial. Для Фазы 2+ если cross-sport арб подтверждается — SportsGameOdds или Betfair Live App Key.

---

## Сводка: что подключаем в Фазе 0/1

### Обязательно сейчас (Фаза 0 — Discovery, Фаза 1 — Backtest)

- **Polymarket Gamma API** — каталог событий и метаданные
- **Polymarket CLOB REST** — orderbook snapshots, prices-history
- **Polymarket Data API** — trade history, leaderboard
- **Polymarket TimeSeries (`/prices-history`)** — для backtest самое важное
- **Dune Analytics API** — готовые dashboards для discovery (десятки community queries уже сделаны)
- **Alchemy Polygon (free)** — для on-chain reconstruction там, где Dune не хватает
- **Anthropic Claude Sonnet 4.6** — primary LLM для исследовательских/анализных задач
- **GDELT 2.0** — geopolitical news features (бесплатно, неограниченно)
- **Tavily** — adhoc web search (1000 free/mo достаточно)

### Опционально в Фазе 1 (если успеваем)

- **Polymarket CLOB WebSocket** — real-time orderbook streaming (для подготовки к paper trading)
- **Kalshi API (read-only)** — cross-platform арб research, политические рынки
- **Manifold Markets API** — sentiment-proxy (бесплатно)
- **The Odds API (free 500/mo)** — для проверки спорт-арба гипотезы
- **Chainstack Polygon (free 3M req)** — fallback к Alchemy
- **Anthropic Haiku 4.5** — для классификации новостей пачками
- **Gemini 3 Flash** — для дешёвого high-volume LLM inference с большим context

### В бэклог на Фазу 2/3

- **The Graph (decentralized) — Polymarket subgraph** — production-grade indexing
- **Goldsky** — если нужны custom subgraphs или Mirror pipelines real-time
- **SportsGameOdds** — если sports-арб гипотеза подтвердилась
- **Reddit API** — sentiment features (после base-strategy)
- **Bitquery Polymarket API** — real-time CTF events
- **DeepSeek / Ollama self-host** — для приватного inference на больших объёмах
- **Polymarket Sports WS** — для sub-100ms спорт-сигналов в Фазе 3

### Игнорируем (не подходит)

- **Twitter / X API** — pay-per-use слишком дорого для high-volume social, легаси тиры $5000/mo не оправданы
- **OddsJam API** — overkill по цене для нашего масштаба
- **Augur** — мёртвый протокол
- **Allium** — enterprise sales, не для нас
- **PredictIt programmatic trading** — нет trading API в принципе, US-only
- **Infura free** — 100k/day слишком мало для backtest
- **QuickNode** — нет постоянного free
- **Public polygon-rpc.com** — нестабильно, не подходит для production reconstruct
- **NewsAPI commercial** — $449/mo за функционал, который GDELT даёт бесплатно
- **EventRegistry** — $600/mo не оправдан в Фазе 0/1

---

## Кросс-категорийные наблюдения

1. **Geo-доступность из РФ.** Большинство API доступны напрямую. Болевые точки: (а) Kalshi требует US-residency для trading; (б) Betfair Exchange — банковский rail из РФ проблематичен (но App Key купить можно); (в) PredictIt — US-only по сути. Polymarket trading работает (нет KYC на контракт-уровне).
2. **Free-tier squeeze в 2025-2026.** Twitter убрал free tier, The Graph deprecate-нул Hosted Service, Polygon public RPC стал требовать sign-in. Тренд однонаправленный — нужно закладывать $200-500/мес на production-pipeline.
3. **LLM ценовая война.** Gemini 3 Flash ($0.50/$3 + 1M context) и DeepSeek V3.2 ($0.27 input) делают массовый news-classification практически free даже на больших объёмах. Claude Opus 4.7 — только когда реально нужен reasoning.
4. **Dune как cheat-code.** Сотни готовых Polymarket-query, можно тащить их через API без своего ingestion. Это сократит Фазу 1 на недели.
5. **Goldsky-как-партнёр-Polymarket.** Сама Polymarket использует Goldsky для real-time. Если когда-нибудь делаем production-pipeline с минимальной latency — стоит туда смотреть.

---

## Источники

### Polymarket
- [Polymarket Rate Limits Documentation](https://docs.polymarket.com/api-reference/rate-limits)
- [Polymarket Rate Limits Guide (AgentBets, March 2026)](https://agentbets.ai/guides/polymarket-rate-limits-guide/)
- [Polymarket API Guide 2026 — CLOB, Gamma & Data API (pm.wiki)](https://pm.wiki/learn/polymarket-api)
- [Historical Timeseries Data — Polymarket Docs](https://docs.polymarket.com/developers/CLOB/timeseries)
- [Polymarket Sports WebSocket](https://docs.polymarket.com/market-data/websocket/sports)
- [Polymarket API for Developers (Chainstack)](https://chainstack.com/polymarket-api-for-developers/)
- [Polymarket Subgraph (GitHub)](https://github.com/Polymarket/polymarket-subgraph)

### Конкуренты
- [Kalshi Rate Limits and Tiers](https://docs.kalshi.com/getting_started/rate_limits)
- [Kalshi API Guide 2026 (pm.wiki)](https://pm.wiki/learn/kalshi-api)
- [Best Prediction Market APIs 2026 (Prediction Hunt)](https://www.predictionhunt.com/blog/best-api-for-prediction-markets)
- [Manifold API Docs](https://docs.manifold.markets/api)
- [Top 10 Prediction Market APIs 2026 (Apidog)](https://apidog.com/blog/top-10-prediction-market-apis-2026/)
- [Best Polymarket Alternatives 2026 (Cryptonews)](https://cryptonews.com/cryptocurrency/polymarket-alternatives/)

### RPC
- [Alchemy Pricing Plans](https://www.alchemy.com/docs/reference/pricing-plans)
- [Alchemy Free Tier Details](https://www.alchemy.com/support/free-tier-details)
- [Chainstack Polygon Pricing](https://chainstack.com/build-better-with-best-price-performance-polygon-nodes/)
- [Chainstack Best Polygon RPC Providers 2026](https://chainstack.com/best-polygon-rpc-providers-2026/)
- [10 Best Polygon RPC Providers 2026 (Dwellir)](https://www.dwellir.com/blog/10-best-polygon-rpc-providers-2025)
- [Public Polygon RPC catalogue (Chainstack)](https://chainstack.com/public-polygon-rpc-complete-endpoint-catalogue/)
- [Polygon RPC Endpoints (official)](https://docs.polygon.technology/pos/reference/rpc-endpoints)

### Indexers
- [Dune Polymarket Dashboards](https://dune.com/polymarket)
- [Dune Pricing](https://dune.com/pricing)
- [The Graph Polymarket Subgraph Guide](https://thegraph.com/docs/en/subgraphs/guides/polymarket/)
- [Goldsky Pricing](https://goldsky.com/pricing)
- [Top 5 hosted Subgraph platforms 2026 (Chainstack)](https://chainstack.com/top-5-hosted-subgraph-indexing-platforms-2026/)
- [Bitquery Polymarket API](https://docs.bitquery.io/docs/examples/polymarket-api/)
- [Flipside Crypto](https://flipsidecrypto.xyz/)

### News & Social
- [NewsAPI Pricing](https://newsapi.org/pricing)
- [Perplexity Pricing 2026 (Finout)](https://www.finout.io/blog/perplexity-pricing-in-2026)
- [Tavily / Search API Pricing Compared 2026](https://awesomeagents.ai/pricing/search-api-pricing/)
- [X (Twitter) API Pricing 2026 (Postproxy)](https://postproxy.dev/blog/x-api-pricing-2026/)
- [Reddit API Pricing 2026 (Octolens)](https://octolens.com/blog/reddit-api-pricing)
- [GDELT Project — Free News Data 2026](https://dataresearchtools.com/gdelt-project-for-news-data-2026-free-alternative-to-newsapi/)
- [Best News APIs 2026 (DataResearchTools)](https://dataresearchtools.com/best-news-apis-comparison/)

### LLM
- [Anthropic API Pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- [Anthropic Claude API Pricing 2026 (Silicon Data)](https://www.silicondata.com/use-cases/anthropic-claude-api-pricing-2026/)
- [Claude API Pricing March 2026 (TLDL)](https://www.tldl.io/resources/anthropic-api-pricing)
- [OpenAI API Pricing](https://openai.com/api/pricing/)
- [LLM API Pricing 2026 — GPT-5, Claude 4, Gemini 2.5, DeepSeek (TLDL)](https://www.tldl.io/resources/llm-api-pricing-2026)
- [AI API Pricing Comparison 2026 (IntuitionLabs)](https://intuitionlabs.ai/articles/ai-api-pricing-comparison-grok-gemini-openai-claude)
- [Ollama Library](https://ollama.com/library)
- [DeepSeek V3 Complete Guide 2026 (SitePoint)](https://www.sitepoint.com/deepseek-v3-complete-guide-deploy-and-optimize-local-ai-in-2026/)

### Sports Odds
- [The Odds API](https://the-odds-api.com/)
- [Odds API Pricing 2026 Comparison (OddsAPI.io)](https://oddspapi.io/blog/odds-api-pricing-2026-comparison/)
- [SportsGameOdds Pricing](https://sportsgameodds.com/pricing)
- [Betfair Exchange API Costs](https://support.developer.betfair.com/hc/en-us/articles/115003864531)
- [Betfair Application Keys](https://docs.developer.betfair.com/display/1smk3cen4v3lu3yomq5qye0ni/Application+Keys)
- [OddsJam Sports Betting API](https://oddsjam.com/odds-api)

---

*Документ создан как часть Phase 0 Discovery. Обновлять при изменении тарифов или появлении новых платформ (раз в квартал минимум).*
