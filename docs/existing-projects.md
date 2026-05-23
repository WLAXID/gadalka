# Существующие проекты для Polymarket

> Phase 0 — Discovery. Что уже есть в экосистеме Polymarket, что переиспользовать как зависимости, какие edges уже вытоптаны конкурентами, куда стоит/не стоит идти проекту **gadalka**.
>
> Дата составления: 2026-05-23. Геолокация исполнителя: РФ (важно — см. секцию «Доступ из РФ» в конце).

## Оглавление

1. [Open-source SDK и клиенты](#1-open-source-sdk-и-клиенты)
2. [Open-source боты на GitHub](#2-open-source-боты-на-github)
3. [Коммерческие SaaS и тулы](#3-коммерческие-saas-и-тулы)
4. [Telegram / Discord / Twitter signal services](#4-telegram--discord--twitter-signal-services)
5. [Академические работы и серьёзные бэктесты](#5-академические-работы-и-серьёзные-бэктесты)
6. [Aggregators и dashboards](#6-aggregators-и-dashboards)
7. [Известные edges и насколько они вытоптаны](#7-известные-edges-и-насколько-они-вытоптаны)
8. [Доступ из РФ — отдельный риск](#8-доступ-из-рф--отдельный-риск)
9. [Рекомендации для gadalka](#9-рекомендации-для-gadalka)
10. [Источники](#10-источники)

---

## 1. Open-source SDK и клиенты

Polymarket поддерживает три официальных «полных» CLOB-клиента (TS, Python, Rust) и пару вспомогательных. В мае 2026 случился крупный переход: **v1-клиенты архивированы**, рекомендован **v2** на новой авторизации (EIP-712 L1 + HMAC L2) и обновлённой ордер-модели CLOB V2.

| Проект | GitHub | Язык | Stars | Last release / status | Что покрывает | Релевантность для gadalka |
|---|---|---|---:|---|---|---:|
| **py-clob-client** (v1) | `Polymarket/py-clob-client` | Python | ~1.2k | v0.34.6 (Feb 2026), **архивирован 11 May 2026** | CLOB REST: orders (limit/FOK), books, midpoint, prices, trade history. Нет WebSocket. | **1** — мёртв, не использовать |
| **py-clob-client-v2** | `Polymarket/py-clob-client-v2` | Python | ~125 | v1.0.1 (May 2026), активен | CLOB V2 REST: limit GTC, market FOK/FAK, cancel, account, EIP-712+HMAC. WS не подтверждён. | **5** — основной кандидат для нас |
| **clob-client-v2** | `Polymarket/clob-client-v2` | TypeScript | — | активен, требует viem | То же что и Python v2 | 2 — мы не TS |
| **rs-clob-client-v2** | `Polymarket/rs-clob-client-v2` | Rust | — | активен | CLOB V2 | 2 — мы не Rust |
| **real-time-data-client** | `Polymarket/real-time-data-client` | TypeScript | — | активен | WebSocket-фид market/user channels | 3 — образец для нашего WS-клиента на Python |
| **polymarket-us-python** | `Polymarket/polymarket-us-python` | Python | — | активен | US-домен (Polymarket US/CFTC). Нам недоступно из РФ. | 1 |
| **builder-relayer-client** (py/ts) | `Polymarket/py-builder-relayer-client` | Python | — | активен | Gasless relayer для builder | 3 — пригодится, чтобы не платить gas |
| **go-builder-signing-sdk** | `Polymarket/go-builder-signing-sdk` | Go | — | активен | Builder header signing | 1 — не Go |
| **polymarket-sdk** (wallets) | `Polymarket/polymarket-sdk` | TS | — | активен | Polymarket Wallets SDK (магический логин, embedded wallet) | 2 — для бэкенд-бота не нужно |

### Сторонние / community SDK

| Проект | Язык | Last commit | Особенности | Релевантность |
|---|---|---|---|---:|
| `pascal-labs/polymarket-sdk` | Python | — | Заявляет execution + WS feeds + on-chain redemption «всё в одном» | **3** — стоит посмотреть, если v2 не покроет WS |
| `anthonyebiner/py-clob-client-extended` | Python | поддерживается на v1 базе | Расширения старого клиента; теряет актуальность с архивом v1 | 1 |
| `anthonyebiner/PolyPython` | Python | старый | Простой wrapper Gamma + CLOB | 1 |
| `quantpylib.wrappers.polymarket` | Python | активен | Async wrapper, нормализованный API: CLOB + Data API + Gamma + WS. Под CLOB V2. | **4** — серьёзный кандидат, если py-clob-client-v2 покажется сырым |
| `dsnchz/polymarket-clob-client` | TypeScript | активен | Полиморфные crypto-API, маленький bundle, для браузера | 1 |
| `cyl19970726/poly-sdk` | TypeScript | активен | Унифицированный TS SDK: trading + market data + smart money + on-chain | 1 |
| `GoPolymarket/polymarket-go-sdk` | Go | активен | HFT-ready, feature-complete | 1 |
| `ivanzzeth/polymarket-go-gamma-client` | Go | активен | Только Gamma | 1 |

### Что не покрывают официальные SDK (важно для архитектуры gadalka)

1. **Gamma API** (метаданные рынков, events, search) — отдельный REST, своих оф. клиентов нет; делается по голым `httpx`-запросам.
2. **Data API** (исторические user activity, leaderboard) — то же, своего SDK нет.
3. **WebSocket** market / user feeds — есть только TS-референс. Для Python — либо `pascal-labs/polymarket-sdk`, либо `quantpylib`, либо свой клиент на `websockets`/`websockets[asyncio]`.
4. **On-chain часть** (CTF: redeem positions, USDC approvals на Polygon) — официальных Python-bindings нет; делаем через `web3.py`.

**Вывод:** для gadalka берём **py-clob-client-v2 как первичный CLOB-клиент**, Gamma/Data API оборачиваем сами тонким слоем на `httpx`, WebSocket пишем сами (на `pascal-labs/polymarket-sdk` или `quantpylib` смотрим как на референс). On-chain — `web3.py`.

---

## 2. Open-source боты на GitHub

Список топ-15 после фильтрации мусора (бесчисленные форки с SEO-стаффингом в названии — отсекаем). Сортировка — по сочетанию stars + полезности кода для нас.

| Репо | Подход | Язык | Stars (порядок) | Last activity | Declared perf | Заметки и переиспользуемость |
|---|---|---|---:|---|---|---|
| `Polymarket/agents` | LLM-агент с RAG: новости + on-chain → решение | Python | **~3.5k** | **архивирован 11 May 2026** | нет | Reference. Стоит изучить структуру `agents/polymarket/gamma.py` — там оф. пример Gamma-запросов. Сам подход (Langchain + Chroma + OpenAI) — heavy и устарел. |
| `warproxxx/poly-maker` | Pure market making, ордера через Google Sheets-конфиг | Python | ~1.2k | низкая | автор: **"in today's market, this bot is not profitable and will lose money"** | Честный референс архитектуры MM. Прямо переиспользовать **нельзя**. |
| `ImMike/polymarket-arbitrage` | Cross-venue (PM↔Kalshi) + bundle (YES+NO<1) + MM, веб-дашборд | Python | средне | активен | нет | **Самый полезный для нас:** есть Polymarket REST+WS клиент, market matcher по text similarity, fee accounting. Архитектура чистая. Хороший образец. |
| `aulekator/Polymarket-BTC-15-Minute-Trading-Bot` | 7-фазная архитектура: multi-signal + risk + self-learning, BTC 15m | Python | средне | активен | бэктест 55–60% winrate (1000 окон), live ≠ paper | Нишевый: только 15-минутные BTC-маркеты. Полезен паттерн риск-менеджмента. |
| `MrFadiAi/Polymarket-bot` | 4 стратегии в одном (включая copy + dashboard + auto-rotation) | Python | средне | v3.1 Jan 2026, активен | нет верифицированной | Хорошо документирован, чисто скрипты. Полезно для UX/CLI-паттернов. |
| `guberm/polymarket-bot` | Claude-ensemble fair value + Kelly sizing + layered risk | Python | малая | активен | нет | LLM-based fair value — экспериментально. Kelly + layered risk — можно подсмотреть. |
| `sonnyfully/polymarket-bot` | **Симулятор** под mispricing / parity / cross-market | Python | малая | активен | нет (sim only) | **Очень полезно для нашего бэктестера.** Не для live. |
| `Drakkar-Software/OctoBot-Prediction-Market` | Плагин к OctoBot: copy trading + arb, GUI | Python | средне | активен | нет | Тяжёлый фреймворк (OctoBot ядро). Не подходит как dependency, но GUI-идеи — да. |
| `al1enjesus/polymarket-whales` | Real-time whale tracker + TG-алерты | Python | малая | активен | нет | Готовый паттерн whale detection — реюзабельный код. |
| `samanalalokaya/polymarket-copy-trading-bot` | Live WS + scale-down position sizing | Python | малая | активен | нет | Реюзабельный pattern для copy-trade. |
| `Trum3it/polymarket-arbitrage-bot` | Rust, ETH/BTC 15m/1h, market-neutral | Rust | малая | активен | нет | Нерелевантно по языку, но архитектурно интересно. |
| `realfishsam/prediction-market-arbitrage-bot` | PM↔Kalshi auto-buy-low/sell-high, на pmxt.dev | TS | малая | активен | dev-блог: $764/день при депозите $200 | Заявленный perf — single anecdotal data point. |
| `CarlosIbCu/polymarket-kalshi-btc-arbitrage-bot` | Узко BTC 1h PM↔Kalshi | Python | малая | активен | нет | Полезный референс «как мэтчить идентичные рынки». |
| `0xalberto/polymarket-arbitrage-bot` | Single/multi-market arb внутри Polymarket | — | малая | активен | нет | Combinatorial-arb идеи. |
| `evan-kolberg/prediction-market-backtesting` | Extension для **NautilusTrader** | Python | малая | активен | нет | **Серьёзно стоит рассмотреть** для бэктеста: NautilusTrader — production-grade engine на Rust+Python. |
| `aulegabriel381` blog + Polymarket+NautilusTrader Medium | Туториал | Python | — | Medium 2025 | бэктест 55–60%, в live 25–27% | Прямо документирует gap бэктест→прод. |

Репозитории с явным SEO-спамом в названии (`zkOSAI`, `PolyBullLabs`, `dev-protocol/polymarket-trading-bot`, `michalstefanow`, `terrytrl100`) — формально открытые, по факту dropshipping-визитки для платных копий. **Игнорируем.**

### Что наиболее переиспользуемо

- **ImMike/polymarket-arbitrage** — API-обёртки, market matcher, fee-accounting блок.
- **sonnyfully/polymarket-bot** — симулятор / backtester (отдельно от live).
- **evan-kolberg/prediction-market-backtesting** + **NautilusTrader** — серьёзный движок.
- **al1enjesus/polymarket-whales** — whale detection pattern.
- **Polymarket/agents** — пример обёртки Gamma API (даже из архива).

---

## 3. Коммерческие SaaS и тулы

Экосистема большая — `pm.wiki` каталогизирует 350+, AgentBets — «полный» каталог, defiprime пишет про 170+. Привожу важное для нашего сегмента (data + signals + analytics + arb).

### Аналитические терминалы и dashboards

| Проект | URL | Что даёт | API? | На кого | Цена | Релевантность |
|---|---|---|---|---|---|---:|
| **Polymarket Analytics** | `polymarketanalytics.com` | Global PnL, trader leaderboard, market stats | да, web + REST | retail + quants | freemium | 3 |
| **PolyScan** | `polyscan.bot` / `polyscan.bet` | Real-time leaderboard, position tracker, terminal | да | retail/pro | freemium | 3 |
| **Verso** | `verso.trading` | Pro-grade prediction market terminal | — | institutional | paid | 2 |
| **Sharpe Terminal** | `sharpeterminal.com` | Bloomberg-style multi-venue | — | pro | paid, beta | 2 |
| **Converge** | `converge.market` | Polymarket + Kalshi + Limitless в одном | — | pro | paid | 2 |
| **Hashdive** | `hashdive.com` | Smart Scores wallets analytics | да (REST) | quants | freemium | 3 |
| **Polysights** | `polysights.xyz` | AI + 30+ custom metrics | да | quants | freemium | 3 |
| **PolyVision** | `polyvisionx.com` | Wallet analyzer + copy trading scores | — | retail | freemium | 2 |
| **FirePolymarket** | `firepolymarket.com` | Fire Score smart-money tracker | — | retail | freemium | 2 |
| **Monitor the Situation** | — | Institutional orderbook dashboard | — | institutional | paid | 2 |

### Data infrastructure (важно для нашего data layer)

| Проект | URL | Что даёт | Цена | Релевантность |
|---|---|---|---|---:|
| **Marketlens** | `marketlens.trade` | **Tick-level historical orderbook + trades, Python SDK + backtest REST** | paid (?) | **4** — основной кандидат на historical data |
| **PolyBackTest** | `polybacktest.com` | Polymarket full L2 orderbook historical 1-min, backtester | paid (?) | **4** — альтернатива |
| **PolySimulator** | `polysimulator.com/backtesting` | PM+Kalshi strategy tester | freemium | 3 |
| **Dome** | `domeapi.io` | Unified API across PM/Kalshi/etc | paid | 3 |
| **PolyRouter** | `polyrouter.io` | Normalized data across venues | paid | 2 |
| **Probalytics** | `probalytics.io` | REST API, 1ms orderbook resolution | paid | 3 |
| **TREMOR** | `tremor.live` | SQL analytics на 140K+ рынков | paid | 3 |

### AI-агенты как SaaS

| Проект | URL | Что даёт | Цена | Заметки |
|---|---|---|---|---|
| **Predly** | `predly.ai` | AI-alerts, заявляет «89% alert accuracy» | paid | Цифра без proof, скептически |
| **Alphascope** | `alphascope.app` | Real-time AI-сигналы | paid | — |
| **Octagon AI** | `octagonai.co` | Deep research + model forecasts | paid | — |
| **Polytrader** | `polytrader.ai` | Auto-trade AI | paid | — |
| **PolyRadar** | `polyradar.io` | 50+ источников, мульти-модель | paid | — |
| **Simmer** | `simmer.markets` | Agent harness + backtesting | paid | — |
| **AgentBets.ai** | `agentbets.ai` | Каталог + гайды по API/ботам | freemium | Хороший источник доки |
| **Laika Labs** | `laikalabs.ai` | Образовательные гайды + (предположительно) signals | — | Контент-маркетинг ferme |
| **Crypticorn** | crypticorn.com | Crypto AI agents marketplace | paid | Не нашёл прямой Polymarket-продукт, в общем экосистема |

### Trading bots / terminals as a service

| Проект | URL | Тип | Цена |
|---|---|---|---|
| **PolyBot** | `polybot.trading` | Self-custodial Telegram bot (Gnosis Safe) | paid |
| **Polycule** | `polycule.trade` | TG mobile trading | paid |
| **Flipr** | `flipr.bot` | DeFi с плечом и lending | paid |
| **okbet** | `tryokbet.com` | TG terminal PM+Kalshi | paid |
| **Polyswipe** | `polyswipe.io` | Tinder-UI | freemium |
| **QuantVPS / TradingVPS** | `quantvps.com` | VPS-хостинг под трейд-ботов + контент-маркетинг | paid |
| **PolyTelegramBot** | `polytelegrambot.com` | Copy-trade Polymarket-only TG | $49–$299 |
| **Polycool** | `polycool.live` | «Bloomberg для prediction markets» через TG | paid |

### Что важно усвоить про коммерческий сегмент

- Рынок **очень фрагментирован и SEO-перенасыщен.** Множество «продуктов» — landing-page с TG-ботом.
- Реальные инструменты, которые экономят месяцы работы для нас, — это **Marketlens / PolyBackTest / Probalytics** для historical data. Если бюджет позволяет, **берём готовый dataset вместо самопальной коллекции.**
- AgentBets.ai + pm.wiki + defiprime guide — лучшие меты-источники, держим в bookmarks.

---

## 4. Telegram / Discord / Twitter signal services

Не подписываемся, просто фиксируем что есть.

| Сервис | Канал/URL | Тип сигналов | Заявленный winrate | Цена |
|---|---|---|---|---|
| **PolyGun** (`@polygunsniperbot`) | TG | Copy-trade top wallets, sniper | заявленный 55%+ для отобранных wallets | freemium + paid |
| **Polytrage** | TG | Arb-alerts каждые 15 мин | — | paid |
| **PolyScalping** | `polyscalping.org` | Scalp-alerts каждые 60 сек | — | paid |
| **alerts.chat** | TG | Customizable | — | paid |
| **YN Signals** | `t.me/YNSignals` | 24/7 alpha aggregation | — | paid |
| **PolySpy** | `t.me/PolySpy_bot` | New market discovery | — | freemium |
| **Polylerts** | `t.me/Polylerts_bot` | До 15 wallets monitoring | — | freemium |
| **Polytracker** | `t.me/polytracker0_bot` | Wallet monitoring | — | freemium |
| **PolyAlertHub** | `polyalerthub.com` | Whale + trend alerts | — | freemium |
| **Wincy Polymarket Bot** | TG | Top-5 position holders на рынок | — | freemium |
| **Nevua Markets** | `nevua.markets` | Watchlists + TG-alerts | — | paid |
| **TradeLabs** | `tradelabs.org` | AI-парс discord/telegram сигналов + автоисполнение через desktop-app | — | paid |
| **ICE Polymarket Signals & Sentiment** | Intercontinental Exchange | Институциональный sentiment-tool | — | enterprise |

Цены, которые удалось найти явно:
- `PolyTelegramBot`: trial 1d $49 / week $99 / month $299
- Generic prediction bot: 7-day trial → $29/мес

### Что говорят аноним. цифры
- «74% accuracy» — одна вирусная цифра по новостно-on-chain боту, без proof.
- «89% alert accuracy» Predly — маркетинговая, не верифицировано.
- Винрейт «отбора leaderboard-trader’ов» — 55%+, и это вход в сам по себе известный copy-trade approach.

**Вывод:** signals-сегмент насыщен, цифры **не верифицированы.** Все «winrate» в публикациях — без статистического обоснования, без backtest-кода, без out-of-sample. Не доверять.

---

## 5. Академические работы и серьёзные бэктесты

| Работа | Где | Год | Ключевая находка | Релевантность |
|---|---|---|---|---:|
| **Reichenbach & Walther — "Exploring Decentralized Prediction Markets: Accuracy, Skill, and Bias on Polymarket"** | SSRN abstract_id=5910522 | Dec 2025 | 124M+ трейдов. **Нет общего longshot bias** на Polymarket. Есть overtrading на default/YES. Цены **слегка обыгрывают букмекеров.** Профитные трейдеры играют больше favorites. | **5** — must-read для нашей модели edges |
| **"The Anatomy of Polymarket: Evidence from the 2024 Presidential Election"** | arXiv 2603.03136 | 2026 | Микроструктура, гибридная децентрализация | 4 |
| **"The Anatomy of a Decentralized Prediction Market: Microstructure Evidence from the Polymarket Order Book"** | arXiv 2604.24366 | 2026 | Структура order book’а | 4 |
| **"Unravelling the Probabilistic Forest: Arbitrage in Prediction Markets"** | arXiv 2508.03474 | 2025 | Формализация: 2 типа арбитража — *market rebalancing* (внутри рынка) и *combinatorial* (между рынками одного event) | **4** — мат. база под наш арбитраж-модуль |
| **"Are Betting Markets Better than Polling in Predicting Political Elections?"** | arXiv 2507.08921 | 2025 | Polymarket > polls в swing-states 2024 | 3 |
| **"Prediction Laundering: The Illusion of Neutrality, Transparency, and Governance in Polymarket"** | arXiv 2602.05181 | 2026 | Критика governance/transparency | 2 |
| **NBER w15923 — "Explaining the Favorite-Longshot Bias"** | NBER | 2010, classic | Базовая теория FLB | 4 |
| **Frenzy Capital — "Trading strategies for prediction markets"** | Medium, Apr 2026 | 2026 | Качественный обзор стратегий | 3 |
| **QuantPedia — "Systematic Edges in Prediction Markets"** | quantpedia.com | 2025+ | Сводка по edges | 3 |

### Большие Medium-статьи с конкретными цифрами

| Статья | Цифра / находка |
|---|---|
| Aule Gabriel — "Building Polymarket BTC 15-min bot with NautilusTrader" | Backtest 1000 BTC-5min окон: 55–60% winrate, ROI 20–50% в год при 1% риск/трейд. **Live: 25–27% winrate.** Gap из-за perfect fills в бэктесте vs 2–4¢ slippage в проде. |
| Jung-Hua Liu — "AI-Augmented Arbitrage in Short-Duration Prediction Markets" | Бэктест: 522× returns. Live: -49.5% капитала. Fee + slippage = 5-min BTC binary **эффективно прайсятся.** |
| Benjamin-Cup — "Unlocking Edges in 5-Minute Crypto Markets" | Last-second dynamics как реальный (но узкий) edge на коротких рынках |
| JIN / The Capital — "The Complete Polymarket Playbook: Finding Real Edges in the $9B Prediction Market Revolution" | Систематический обзор edges; cross-platform $2-5% spreads документированы |
| MONOLITH — "Prediction Markets 2025: PM, Kalshi, and the Next Big Rotation" | Макро-обзор venue rotation |
| "Beyond Simple Arbitrage" (Medium ILLUMINATION) | 4 стратегии, которые «реально работают в 2026» (последняя минута, news lag, MM на дальних рынках, combinatorial arb) |
| `0x_Discover` X-thread | "Backtested 72M PM+Kalshi trades, 27k предсказаний, +$805K PnL" — заявлено, без проверяемого кода |

**Главный академический take для gadalka:** свежий paper Reichenbach & Walther 2025 **опровергает наивную ставку на FLB-edge** на Polymarket. На спорте/скачках FLB железный, но на Polymarket — нет общего. То, что работает у профитных трейдеров, — **играть favorites чаще,** а не longshots. Это контр-интуитивно и важно.

---

## 6. Aggregators и dashboards

| Категория | Лучшие | Что брать |
|---|---|---|
| **Whale / smart money tracking** | PolyScan, FirePolymarket, Polymarket Bros (>$4k trades), polymarket-whales (OSS) | PolyScan для UI; OSS-репо для собственного кода |
| **Positions trackers** | Polymarket Analytics, PolymarketDash, PredictingTop, PolyVision | Polymarket Analytics — самый полный |
| **Market discovery / search** | Matchr (1500+ markets), Prediction Index (140+ проектов), TREMOR (140k markets, SQL) | TREMOR для quant-исследования |
| **On-chain whale alerts** | polymarket-whales (OSS), WhaleSight (browser ext), Stand | OSS reuse-friendly |
| **Каталоги экосистемы** | pm.wiki (350+ projects), AgentBets.ai (ecosystem map), defiprime guide (170+), Awesome-Prediction-Market-Tools (aarora4), Awesome-Polymarket-Tools (harish-garg) | pm.wiki + AgentBets = bookmark обязательно |

---

## 7. Известные edges и насколько они вытоптаны

Subjective rating: **5 = битком, не входить**; **1 = свободно, но малая вместимость рынка**.

### 7.1 Favorite-longshot bias (FLB)

- **Что говорят данные:** на скачках/спорте — классический FLB железный. **На Polymarket** свежее исследование 124M трейдов (Reichenbach & Walther 2025) **не находит общего FLB.** Есть локальные эффекты: overtrading "Yes"/default, и профитные трейдеры **играют больше favorites** (т.е. ловят оверпрайс на longshots).
- **Кто уже эксплуатирует:** все systematic-quant-боты Polymarket. Это публично описано в десятках Medium-статей.
- **Размер edge:** 1–3¢ на контракт, преимущественно в политике/спорте.
- **Вытоптанность: 4/5.** Не идём в чистом виде. Можем — как одну фичу в ансамбле модели на крайних ценах (≤5¢ / ≥95¢) с учётом конкретной категории рынка.

### 7.2 Cross-platform arbitrage Polymarket vs Kalshi/PredictIt/Limitless

- **Жив ли edge:** да, но **сжатый.** Спреды 2–5¢ документированы в 15–20% идентичных рынков (часть остаётся persistent на политических темах). 12–20% месячных при liquidity-aware execution — заявлено в гайдах (Leviathan News, laikalabs.ai).
- **Кто ловит:** ImMike OSS, realfishsam, pmxt.dev, ArbBets, Eventarb, Polytrage и сотня бот-сервисов. Avg время жизни возможности **2.7 секунды (в 2024 было 12.3).**
- **Главное узкое место:** 73% арбитражной прибыли уходит ботам с sub-100ms execution. Cloudflare-троттлинг Polymarket = 4k/10s на Gamma, 15k/10s общий.
- **Также:** 78% арб-возможностей на low-volume рынках **проваливаются на исполнении** (2025 study).
- **Вытоптанность: 5/5 для крупных high-volume markets (BTC/ETH, основные политики).** **3/5 для никовых длинных событий**, где execution latency некритичен.
- **Возможный заход:** не в HFT-арб BTC 15m (там uniform проиграем), а в **long-tail combinatorial arb** на рынках, где event имеет 5+ исходов и сумма вероятностей дрейфует.

### 7.3 News → market lag

- **Окно:** **8 минут в публичных кейсах, среднее «эмоциональное окно» 30–60 секунд.** Реальные кейсы — Trump witness recant 14 Jan 2026, репрайс за 8 минут, кейс с $896 profit на $2k позиции.
- **Кто ловит:** все, у кого есть NLP-пайплайн (вторая половина SaaS-AI листинга выше). HFT-ботам нужно sub-100ms.
- **Размер edge:** 5–13¢ при крупных новостях; на мелких — 1–3¢.
- **Конкурентный барьер:** **низкий по структуре, высокий по операционке** (нужны feeds GDELT/Twitter/Bloomberg, латентность доставки, NLP с расщеплением «материально/нет»).
- **Вытоптанность: 4/5 на топовых событиях** (всё, что попадает в общие feeds). **2–3/5 на нишевых event-категориях** (sport-mid-tier, regional politics, science/space), куда NLP-пайплайны конкурентов «не нацелены».
- **Перспективный заход для gadalka:** не пытаться обогнать топ-ботов на CNN/Twitter трендах, а **строить специализированные feeds** (academic, niche policy, science RSS) под определённые категории рынков.

### 7.4 Twitter sentiment → fade retail

- **Идея:** retail приходит после новости в Twitter, гонит цену слишком далеко — fade.
- **Реальность 2026:** Twitter (X) **больше не самостоятельный сигнал**, а часть общего news flow. ICE запустил институциональный Polymarket sentiment tool — то есть сегмент институционализирован.
- **Кто ловит:** все AI-агенты (Predly, Alphascope, Polytrader, Polybro, PolyRadar). И ICE.
- **Вытоптанность: 5/5 в общем виде.** Подход «купи противоположное retail» — наивен.
- **Что ещё может работать:** не fade-вообще, а **fade на specific structural overreactions** (например, известный паттерн «русскоязычный/неанглоязычный Twitter тренд → задержка прайсинга на 2–5 минут»). Это операционно дорого.

### 7.5 Long-tail mispricing (low-volume markets)

- **Идея:** на рынках с дневным volume <$10k цены неэффективны, потому что arb-боты их игнорируют (execution слишком дорог).
- **Реальность:** edges реально шире (5–15¢), но **78% попыток арбитража fail** из-за slippage (study 2025). Liquidity <$1k — «избегать», по консенсусу гайдов.
- **Кто ловит:** мало кто эффективно. ImMike мониторит 10k+ рынков, но execution на low-volume — известная проблема. Models, которые умеют **patiently market-make** на этих рынках (poly-maker), сейчас убыточны из-за конкуренции и slippage.
- **Вытоптанность: 2/5.** Реально свободно, но **размер пирога мал** и execution-cost — потолок.
- **Перспективный заход:** **наш потенциальный main edge.** Не HFT, а patient limit-orders + риск-ограничение + статистическая selection рынков, где можно держать позицию до резолва.

### 7.6 Бонусы — менее обсуждаемые edges

- **Last-second dynamics на коротких рынках (5–15 min crypto).** Описано в Benjamin-Cup Medium. Жив, но HFT-инструмент, конкуренция плотная. Вытоптанность 4/5.
- **Combinatorial arb внутри event с N исходами.** Описан в arXiv 2508.03474. Меньше ботов на это направлено. Вытоптанность 3/5.
- **Passive liquidity underwriting (rebates).** Работает на Kalshi из-за модели maker rebates, на Polymarket — слабее. Вытоптанность 3/5.

---

## 8. Доступ из РФ — отдельный риск

Это не «существующий проект», но критически важно для архитектурных решений:

- **РФ в OFAC-блок-листе Polymarket.** Доступ заблокирован, **бан перманентный.**
- **VPN запрещён ToS**, есть active geo-detection beyond IP (browser fingerprint, поведенческие сигналы). Аккаунты **замораживают** — задокументированы случаи зависших выводов.
- **Что это значит для gadalka:**
  - on-chain взаимодействие (Polygon CTF + USDC + ордера CLOB v2 через подписи EIP-712) **технически работает с любого IP**, потому что подпись — клиентская, а MM/CLOB-Match (gnosis safe / proxy wallet) — on-chain.
  - Геоблок бьёт по веб-фронту и (возможно) по части REST/Gamma endpoint’ов с server-side гео-проверкой.
  - Реальный риск — **compliance freeze withdrawals при KYC/проверке.** Если KYC не пройден, выводов нет. Если пройден — гражданство РФ = бан в любом случае.
- **Это означает, что gadalka — research/paper-trading проект для отработки edges + (опционально) исполнение через прокси-юрисдикцию.** Любое использование production должно явно фиксироваться в risk-секции плана.

---

## 9. Рекомендации для gadalka

### 9.1 SDK / стек данных

| Решение | Что берём | Почему |
|---|---|---|
| CLOB client | **`py-clob-client-v2`** | официальный, активный, MIT, рекомендован в docs |
| Gamma + Data API | **свой тонкий `httpx`-wrapper** | оф. SDK нет; код тривиален |
| WebSocket | **свой клиент на `websockets`** + референс `Polymarket/real-time-data-client` (TS) и `pascal-labs/polymarket-sdk` (Python) | оф. Python-WS клиента нет, но образцы есть |
| On-chain (CTF, USDC) | **`web3.py`** | стандарт; есть `python-order-utils` от Polymarket для подписи ордеров |
| Async-фреймворк-обёртка (опционально) | **посмотреть `quantpylib.wrappers.polymarket`** перед написанием своего | если устроит — экономим неделю |
| Historical data | **закупить Marketlens или PolyBackTest** вместо самосбора | сэкономит месяцы L2-orderbook коллекции |
| Backtest engine | **NautilusTrader** + `evan-kolberg/prediction-market-backtesting` extension | production-grade, прозрачно учитывает slippage/fills |
| Стек обработки | `httpx`, `duckdb`, `polars` (как в requirements проекта) | подтверждаем |

### 9.2 Что **переиспользовать** из OSS

1. **`ImMike/polymarket-arbitrage`** — изучить структуру: market matcher по text similarity, fee accounting, абстрагированный REST+WS клиент. Можно частично копировать (MIT-like).
2. **`Polymarket/agents`** (даже архивный) — `agents/polymarket/gamma.py` как референс Gamma-запросов.
3. **`al1enjesus/polymarket-whales`** — паттерн whale-detection.
4. **`sonnyfully/polymarket-bot`** — симулятор отдельно от live.
5. **`anthropic-style RAG`** (если AI-edge) — **не** Langchain+Chroma как в `Polymarket/agents`, это устарело. Прямые вызовы LLM + локальный vector store (или вообще без).

### 9.3 Что **НЕ делать** (вытоптано)

- ❌ **HFT cross-platform arb на крупных рынках (BTC/ETH 5–60 min, top-tier политики)** — 2.7s avg time-to-fill, sub-100ms клуб, infrastructure-game. Не пройдём latency.
- ❌ **Чистый FLB exploit** на Polymarket — академически опровергнуто на 124M трейдов.
- ❌ **Twitter-sentiment fade retail** в общем виде — институционализирован (ICE), все ИИ-агенты делают.
- ❌ **Pure market making на ликвидных рынках** — автор poly-maker честно говорит «не прибыльно сейчас».
- ❌ **5–15-минутные BTC binary** — известно про gap бэктест→прод (522× → -49.5%; 55% → 25%). Fee+slippage съедают edge.
- ❌ Подписки на signal-services с заявленным winrate без proof — мусор.

### 9.4 Куда **идти** (наименее вытоптанные ниши)

1. **Long-tail mispricing + patient market making на mid-volume рынках** ($10k–$100k daily volume, не топ, не помойка). Конкуренция средняя; edge структурный, не HFT-зависимый. *Главный кандидат.*
2. **News-lag в нишевых категориях** (science, regional politics, academic awards, sport mid-tier), где NLP-пайплайны мейнстрим-конкурентов слабые. Требует своих feeds, но конкуренция там тоньше.
3. **Combinatorial arb** в events с 5+ исходами (arXiv 2508.03474). Математически чисто; реализация сложна, отсюда меньше конкурентов.
4. **Specialised models на категориях** (например, спорт-mid-tier, scientific events) — где основной FLB-эффект **может** проявляться локально, в отличие от общего рынка.
5. **Selective copy-trading** топ-wallets с **собственным filtering** (не наивный mirror): только wallets с >12 мес activity и не на коротких BTC-рынках. Здесь edge — не сам сигнал, а selection.

### 9.5 Сводка по приоритетам Phase 0

1. Поставить `py-clob-client-v2`, написать `httpx`-wrappers для Gamma/Data, WebSocket-клиент.
2. Сравнить `quantpylib` с собственным wrap-слоем — выбрать одно.
3. Сделать demo Gamma → выгрузка событий → DuckDB.
4. Скачать paper Reichenbach & Walther 2025 и arXiv 2508.03474 — это база.
5. Изучить код `ImMike/polymarket-arbitrage` и `Polymarket/agents:agents/polymarket/gamma.py` детально.
6. Решить вопрос с historical data: **Marketlens/PolyBackTest** vs. самосбор.
7. Финализировать стратегическое направление по 9.4 (вероятно — long-tail mid-volume + news-lag в niche).

---

## 10. Источники

### Официальные SDK

- [Polymarket/py-clob-client (архивный)](https://github.com/Polymarket/py-clob-client)
- [Polymarket/py-clob-client-v2](https://github.com/Polymarket/py-clob-client-v2)
- [Polymarket/clob-client-v2](https://github.com/Polymarket/clob-client-v2)
- [Polymarket/real-time-data-client](https://github.com/Polymarket/real-time-data-client)
- [Polymarket/agents (архивный)](https://github.com/polymarket/agents)
- [Polymarket Clients & SDKs docs](https://docs.polymarket.com/api-reference/clients-sdks)
- [Polymarket API Rate Limits](https://docs.polymarket.com/quickstart/introduction/rate-limits)
- [Polymarket Geographic Restrictions](https://help.polymarket.com/en/articles/13364163-geographic-restrictions)

### Open-source боты

- [ImMike/polymarket-arbitrage](https://github.com/ImMike/polymarket-arbitrage)
- [warproxxx/poly-maker](https://github.com/warproxxx/poly-maker)
- [aulekator/Polymarket-BTC-15-Minute-Trading-Bot](https://github.com/aulekator/Polymarket-BTC-15-Minute-Trading-Bot)
- [MrFadiAi/Polymarket-bot](https://github.com/MrFadiAi/Polymarket-bot)
- [Drakkar-Software/OctoBot-Prediction-Market](https://github.com/Drakkar-Software/OctoBot-Prediction-Market)
- [guberm/polymarket-bot](https://github.com/guberm/polymarket-bot)
- [sonnyfully/polymarket-bot](https://github.com/sonnyfully/polymarket-bot)
- [al1enjesus/polymarket-whales](https://github.com/al1enjesus/polymarket-whales)
- [evan-kolberg/prediction-market-backtesting](https://github.com/evan-kolberg/prediction-market-backtesting)
- [realfishsam/prediction-market-arbitrage-bot](https://github.com/realfishsam/prediction-market-arbitrage-bot)
- [CarlosIbCu/polymarket-kalshi-btc-arbitrage-bot](https://github.com/CarlosIbCu/polymarket-kalshi-btc-arbitrage-bot)
- [0xalberto/polymarket-arbitrage-bot](https://github.com/0xalberto/polymarket-arbitrage-bot)
- [Trum3it/polymarket-arbitrage-bot](https://github.com/Trum3it/polymarket-arbitrage-bot)
- [pascal-labs/polymarket-sdk](https://github.com/pascal-labs/polymarket-sdk)
- [quantpylib.wrappers.polymarket](https://quantpylib.hangukquant.com/wrappers/polymarket/)

### Каталоги экосистемы

- [pm.wiki — Polymarket project directory (350+)](https://pm.wiki/projects/polymarket)
- [AgentBets.ai Polymarket Bot Marketplace](https://agentbets.ai/platforms/polymarket-bots/)
- [Awesome-Prediction-Market-Tools (aarora4)](https://github.com/aarora4/Awesome-Prediction-Market-Tools)
- [Awesome-Polymarket-Tools (harish-garg)](https://github.com/harish-garg/Awesome-Polymarket-Tools)
- [defiprime — Definitive Guide to Polymarket Ecosystem (170+)](https://defiprime.com/definitive-guide-to-the-polymarket-ecosystem)

### Academia & research

- [Reichenbach & Walther — Exploring Decentralized Prediction Markets (SSRN, 2025)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5910522)
- [The Anatomy of Polymarket — 2024 Election (arXiv 2603.03136)](https://arxiv.org/html/2603.03136v1)
- [Microstructure Evidence from Polymarket Order Book (arXiv 2604.24366)](https://arxiv.org/html/2604.24366v1)
- [Unravelling the Probabilistic Forest: Arbitrage in Prediction Markets (arXiv 2508.03474)](https://arxiv.org/abs/2508.03474)
- [Are Betting Markets Better than Polling (arXiv 2507.08921)](https://arxiv.org/abs/2507.08921)
- [Prediction Laundering (arXiv 2602.05181)](https://arxiv.org/html/2602.05181v1)
- [NBER w15923 — Favorite-Longshot Bias](https://www.nber.org/system/files/working_papers/w15923/w15923.pdf)
- [QuantPedia — Systematic Edges in Prediction Markets](https://quantpedia.com/systematic-edges-in-prediction-markets/)

### Medium / blog backtests

- [Aule Gabriel — Polymarket BTC 15m Bot with NautilusTrader](https://medium.com/@aulegabriel381/the-ultimate-guide-building-a-polymarket-btc-15-minute-trading-bot-with-nautilustrader-ef04eb5edfcb)
- [Jung-Hua Liu — AI-Augmented Arbitrage in Short-Duration Prediction Markets](https://medium.com/@gwrx2005/ai-augmented-arbitrage-in-short-duration-prediction-markets-live-trading-analysis-of-polymarkets-8ce1b8c5f362)
- [Benjamin-Cup — Unlocking Edges in 5-Minute Crypto Markets](https://medium.com/@benjamin.bigdev/unlocking-edges-in-polymarkets-5-minute-crypto-markets-last-second-dynamics-bot-strategies-and-db8efcb5c196)
- [JIN — The Complete Polymarket Playbook ($9B Market)](https://medium.com/thecapital/the-complete-polymarket-playbook-finding-real-edges-in-the-9b-prediction-market-revolution-a2c1d0a47d9d)
- [MONOLITH — Prediction Markets 2025](https://medium.com/@monolith.vc/prediction-markets-2025-polymarket-kalshi-and-the-next-big-rotation-c00f1ba35d13)
- [Beyond Simple Arbitrage — ILLUMINATION](https://medium.com/illumination/beyond-simple-arbitrage-4-polymarket-strategies-bots-actually-profit-from-in-2026-ddacc92c5b4f)
- [Frenzy Capital — Trading Strategies for Prediction Markets](https://medium.com/@FrenzyCapital/trading-strategies-for-prediction-markets-4025a050e2e2)

### Commercial / SaaS / data

- [Polymarket Analytics](https://polymarketanalytics.com/)
- [PolyScan](https://polyscan.bot/leaderboard)
- [Marketlens](https://marketlens.trade/)
- [PolyBackTest](https://polybacktest.com/)
- [PolySimulator](https://polysimulator.com/backtesting)
- [Dome API](https://domeapi.io/)
- [Probalytics](https://probalytics.io/)
- [TREMOR](https://tremor.live/)
- [Laika Labs — Polymarket Kalshi Arbitrage Guide 2026](https://laikalabs.ai/prediction-markets/polymarket-kalshi-arbitrage-guide)
- [Laika Labs — Prediction Market Biases](https://laikalabs.ai/prediction-markets/prediction-market-biases-how-to-exploit-profit)
- [Laika Labs — Restricted Countries](https://laikalabs.ai/prediction-markets/polymarket-restricted-countries-list)
- [QuantVPS — Polymarket HFT AI Arbitrage Mispricing](https://www.quantvps.com/blog/polymarket-hft-traders-use-ai-arbitrage-mispricing)
- [QuantVPS — Automated Trading on Polymarket](https://www.quantvps.com/blog/automated-trading-polymarket)
- [AgentBets — Polymarket Rate Limits Guide](https://agentbets.ai/guides/polymarket-rate-limits-guide/)
- [AgentBets — Polymarket API Tutorial](https://agentbets.ai/guides/polymarket-api-guide/)
- [pm.wiki — Polymarket API Guide 2026](https://pm.wiki/learn/polymarket-api)
- [Trevor Lasn — How PM-Kalshi Arbitrage Works](https://www.trevorlasn.com/blog/how-prediction-market-polymarket-kalshi-arbitrage-works)
- [AhaSignals Lab — Cross-Platform Arbitrage Strategies](https://ahasignals.com/research/prediction-market-arbitrage-strategies/)
- [Markets Media — ICE Polymarket Signals & Sentiment](https://www.marketsmedia.com/ice-launches-polymarket-signals-and-sentiment-tool/)
- [Finance Magnates — Prediction Markets Are Turning Into a Bot Playground](https://www.financemagnates.com/trending/prediction-markets-are-turning-into-a-bot-playground/)
- [PredictEngine — Best Polymarket Discord/Telegram Groups](https://www.predictengine.ai/blog/polymarket-discord-telegram)
- [CoinCodeCap — Top 10 Polymarket Alert Bots May 2026](https://signals.coincodecap.com/polymarket-alert-bots)

---

*Документ — рабочий артефакт Phase 0. Список тулов экосистемы устаревает быстро (новые SaaS появляются еженедельно); ревизия — при заходе в Phase 1.*
