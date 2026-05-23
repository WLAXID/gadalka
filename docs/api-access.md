# 🔑 API Access — что доступно для gadalka

> Карта доступа: что бесплатно/публично, какие ключи у пользователя есть, что отбросили.
> Обновлено: 2026-05-23

---

## 🟢 Публичные / keyless (без регистрации и ключей)

| API | Что даёт | База |
|-----|----------|------|
| **Polymarket Gamma** | metadata рынков и событий | `https://gamma-api.polymarket.com` |
| **Polymarket CLOB** (read) | orderbook, price, midprice, current trades | `https://clob.polymarket.com` |
| **Polymarket Data API** | historical trades, positions, holders | `https://data-api.polymarket.com` |
| **Polymarket TimeSeries** (`/prices-history`) | исторические цены контрактов | через Gamma |
| **GDELT** | новости в открытом доступе | через BigQuery (есть публичный SQL endpoint) |

## 🔑 Есть ключи (личные, в `.env`)

| API | Зачем для gadalka | Use case |
|-----|--------------------|----------|
| **Chainstack** | Polygon RPC, archive nodes | fallback для price-history через event logs CTF Exchange |
| **Alchemy** | Polygon RPC, второй провайдер | backup к Chainstack, нагрузка/failover |
| **Dune Analytics** | SQL-queries по on-chain Polymarket | **главный источник** historical price-curves в Day 4 |
| **Polygon.io** | прайсы BTC/ETH/stocks | **критично для Crypto-категории Polymarket** (резолв по реальной цене), reference price feed |
| **Tavily** | web search + extract | news ingestion для Фазы 1+ news-lag стратегии |
| **NewsAPI** | заголовки и статьи | дополнительный новостной поток |
| **The Odds API** | котировки спорт-букмекеров (Pinnacle и др.) | арбитраж со sport-категорией Polymarket |
| **Perplexity API** | LLM с web search | news → P(yes) features, исследовательский поиск |

## ❌ Отброшено

| API | Причина |
|-----|---------|
| **Goldsky** | избыточно — есть Chainstack + Alchemy + Dune |
| **Kalshi** | пока не делаем cross-platform арб с US-only маркетами |
| **The Graph Hosted** | deprecated в 2026 |
| **Polygon public RPC** | throttle 40 req/s, требует sign-in |
| **Twitter/X API** | дорого с 2026 ($0.005/read pay-per-use) |
| **PredictIt** | US-only, мизерный объём |
| **Anthropic/OpenAI/Gemini direct** | используем Perplexity для search-augmented |

---

## 📦 Что складываем в `.env`

```
# Polygon RPC
CHAINSTACK_POLYGON_RPC=https://...
ALCHEMY_POLYGON_RPC=https://polygon-mainnet.g.alchemy.com/v2/...

# Аналитика
DUNE_API_KEY=...
POLYGON_IO_API_KEY=...

# Новости и web
TAVILY_API_KEY=...
NEWSAPI_KEY=...
PERPLEXITY_API_KEY=...

# Котировки спорта
ODDS_API_KEY=...
```

`.env` — в `.gitignore` (уже сделано). Никогда не коммитим.

## 🗺️ Дорожная карта подключений

### Day 2 (Фаза 0)
- Полная разведка Polymarket публичных API (Gamma/CLOB/Data/TimeSeries)
- Проверка работы из РФ без VPN
- Базовый HTTP-клиент с rate-limit

### Day 3 (Фаза 0)
- Сборщик metadata через Gamma
- Сохранение в DuckDB

### Day 4 (Фаза 0) — критичный
- **Dune Analytics** как первый источник price-history (готовые queries по Polymarket)
- **Chainstack/Alchemy** как fallback через on-chain event logs
- **Polymarket TimeSeries `/prices-history`** для топ-рынков

### Фаза 1 (бэктест)
- **Polygon.io** для reference price feed (BTC/ETH) — крипто-рынки Polymarket резолвятся по этим ценам
- **NewsAPI / Tavily / Perplexity** — feature engineering для news-lag гипотезы
- **GDELT** через BigQuery — большой бесплатный новостной dataset

### Фаза 1+ опционально
- **The Odds API** — если решим тестировать арб со sport-категорией Polymarket

---

## 🔒 Безопасность ключей

- Все ключи только в `.env`
- `.env` в `.gitignore` (текущий список достаточен)
- При случайной утечке ключа в commit — немедленно revoke + ротация
- Для каждого API подключаемого впервые — проверять free tier лимиты, чтобы не упереться в платный biling неожиданно
