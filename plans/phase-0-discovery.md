# 🔍 Фаза 0 — Discovery

**Срок:** 4–6 дней
**Капитал:** $0
**Статус:** 🟡 На верификации пользователя

---

## 🎯 Цели фазы

К концу фазы мы должны иметь точные ответы на:

1. **Какие данные** доступны бесплатно через Polymarket API без auth?
2. **Какие ещё API** имеет смысл подключить (новости, конкуренты-маркеты для арб, on-chain, LLM)?
3. **Какие готовые проекты** уже работают на Polymarket — что они делают, насколько эффективно, что переиспользовать, а что не изобретать?
4. **Можно ли** реконструировать **price-history до резолва** для исторических рынков? (критично для бэктеста)
5. **Сколько** резолвнутых рынков в каждой категории, какая ликвидность, fees, спред?
6. **Какие 2–3 кандидата edge-гипотез** имеют смысл тестировать в Фазе 1?
7. **Geo-доступность:** работает ли read-only API из РФ без VPN?
8. **Go/no-go:** идём ли в Фазу 1?

## 🚫 Что мы НЕ делаем в Фазе 0

- ❌ Не пишем торгового бота
- ❌ Не строим ML-модель (это Фаза 1)
- ❌ Не делаем full бэктест (только feasibility check)
- ❌ Не заводим деньги, не подключаем кошелёк
- ❌ Не делаем UI / dashboard'ы

Цель фазы — **знание**, а не код. Код только тот, что нужен для добычи знания.

---

## 📅 День 1 — Landscape research: APIs + существующие проекты

> С этого дня начинаем. Прежде чем кодить — понять экосистему.

### Часть А — Каталог API, которые могут пригодиться

Для каждого API: URL, что даёт, цена/freе tier, лимиты, нужен ли auth, оценка «зачем нам».

**Категории API для исследования:**

1. **Polymarket собственные** (3 API)
   - Gamma — metadata рынков и событий
   - CLOB — orderbook, цены, current trades
   - Data API — historical trades, positions, holders
   - TimeSeries — price-history (КРИТИЧНО, см. Day 4)

2. **Конкуренты-маркеты** (для cross-platform арбитража в будущем)
   - Kalshi — US-регулируемый prediction market, REST + WS
   - PredictIt — академический, малый объём, но другой ценовой режим
   - Manifold Markets — play-money + реальные деньги
   - Polymarket-Sports — спортивные рынки

3. **On-chain data (Polygon)**
   - Alchemy free tier
   - Chainstack free tier
   - Public Polygon RPC nodes
   - QuickNode
   - → нужны для backup-источника price-history через event logs CTF Exchange

4. **On-chain indexers**
   - Dune Analytics (free tier + community queries)
   - Goldsky
   - The Graph / Subgraphs для Polymarket
   - Allium / Flipside Crypto

5. **Новости и social**
   - NewsAPI
   - Tavily Search API
   - Perplexity API
   - Twitter/X API (платный, дорогой)
   - Reddit API (бесплатный)
   - RSS-агрегаторы (Feedly, custom)

6. **LLM API** (для news → P(yes) features в Фазе 1)
   - Anthropic Claude
   - OpenAI
   - Google Gemini
   - Локальные (Ollama) — для дешёвой обработки большого потока

7. **Котировки для арбитража со спорт-букмекерами**
   - Pinnacle (через сторонние агрегаторы)
   - Betfair Exchange API
   - The Odds API
   - → если будем смотреть Sports/Politics overlap

### Часть Б — Существующие проекты для Polymarket

Для каждого проекта: ссылка, статус (active/dead), подход, declared performance, открытый код или нет, что переиспользовать.

**Что ищем:**

1. **Open-source SDK/клиенты**
   - `py-clob-client` (официальный)
   - `polymarket-py` сторонние
   - SDK на других языках (JS/TS, Go, Rust, OCaml)
   - → возможно, не пишем HTTP-клиент с нуля

2. **Open-source боты на GitHub**
   - Топ-репозитории по запросам «polymarket bot», «polymarket trading», «polymarket arbitrage»
   - Их подходы: arb, statistical, news-based, signals
   - Заявленный winrate / PnL (если есть)
   - Активность (последний commit, issues, stars)
   - Какие edges они эксплуатируют

3. **Коммерческие тулы / SaaS**
   - Polymarket Analytics
   - pm.wiki
   - AgentBets.ai
   - Crypticorn
   - Laika Labs
   - TradingVPS
   - → стоимость, что дают, есть ли API

4. **Telegram-каналы / signals services**
   - Заявленный winrate
   - Тип сигналов (longshot, arb, news)
   - Бесплатные / платные
   - → понять, какие edges рынок ОСОЗНАЁТ (на которых уже много игроков)

5. **Академические работы и блог-посты**
   - Medium-статьи с конкретными бэктестами
   - arXiv по prediction markets
   - Известные исследования mispricing на Polymarket
   - → бесплатный transfer learning

6. **Aggregators и dashboards**
   - Polymarket positions trackers
   - Smart money trackers для prediction markets
   - On-chain whales на Polymarket

### Методика дня

- Поиск через web search (`polymarket bot github`, `polymarket arbitrage strategy 2026`, `polymarket prediction market alpha`)
- GitHub Trending / topic browsing (`#polymarket`, `#prediction-markets`)
- HackerNews / Reddit (`r/algotrading`, `r/polymarket`)
- Медиум, Substack посты с реальными бэктестами
- Проверка свежести: репозитории без commits >12 мес → закрашиваем, но фиксируем подход
- Для каждого инструмента — оценка «инсайт-плотности»: если статья просто пересказывает API docs — мимо

### Артефакты

- `docs/apis-landscape.md` — таблица всех API (категория, URL, free tier, лимиты, auth, оценка для gadalka)
- `docs/existing-projects.md` — таблица проектов (название, ссылка, статус, подход, declared edge, что переиспользовать)
- `docs/competing-edges.md` — какие edges уже известны рынку (favorite-longshot bias, cross-platform arb, news lag, ...) и насколько они «вытоптаны»
- `plans/phase-0-research-notes.md` — выводы и решения: что подключаем в Фазе 0/1, что в бэклог, что игнорируем

### Критерии завершения дня

- ✅ Таблица из ≥15 API с оценкой релевантности для gadalka
- ✅ Таблица из ≥10 существующих проектов / SDK / signal services
- ✅ Список ≥3 конкретных edges, уже известных рынку, с оценкой «насколько вытоптано»
- ✅ Решение: используем существующий SDK для Polymarket или пишем свой клиент
- ✅ Список новых гипотез, появившихся из чтения чужого опыта

---

## 📅 День 2 — API exploration + dataset map

### Задачи

- [ ] Поднять Python 3.11+ venv, поставить `requirements.txt`
- [ ] Базовый HTTP-клиент `src/api/client.py` (или адаптер к найденному в Day 1 SDK):
  - `httpx.AsyncClient` с пулом
  - Rate-limiter (token bucket): отдельные buckets под Gamma/CLOB/Data
  - Retry с exponential backoff на 429/5xx (`tenacity`)
  - Локальный кэш ответов в `data/cache/` (по URL + params хэш) — экономит при пере-проходах
- [ ] **Sample-запросы** ко всем трём API Polymarket:
  - Gamma: `/markets`, `/markets?closed=true`, `/events`, `/events?closed=true`, `/markets/search`
  - CLOB: `/markets/{condition_id}`, `/book`, `/price`, `/midpoint`, `/trades`
  - Data: `/trades`, `/positions`, `/holders`, `/value`
  - TimeSeries: `/prices-history?market={id}&interval=1h`
- [ ] Задокументировать поля каждого ответа в `docs/api-schemas.md`
- [ ] Проверить geo-блок: работает ли API без VPN из текущей локации

### Артефакты

- `src/api/client.py` — базовый клиент с rate-limit
- `notebooks/01_api_exploration.ipynb` — все sample-запросы с пояснениями
- `docs/api-schemas.md` — схемы полей, типы, какие поля иногда пустые
- `docs/geo-check.md` — что работает с какой локации

### Критерии завершения дня

- ✅ Все три API дают ответы, схемы зафиксированы
- ✅ Rate-limit логика проверена (намеренно прёмся в 429, ловим, восстанавливаемся)
- ✅ Geo-block ситуация ясна

---

## 📅 День 3 — Historical markets collector

### Задачи

- [ ] Сборщик `src/collectors/gamma_markets.py`:
  - Пагинация по `/markets?closed=true` (limit/offset, до конца)
  - Параллельно собирать `/events` для context
  - Сохранять в `data/raw/markets_<date>.parquet` (одна запись = один market)
  - Минимум полей: `id`, `condition_id`, `slug`, `question`, `category`, `outcomes`, `end_date`, `closed_at`, `volume_total`, `liquidity`, `fee_rate`, `resolution_outcome`, `resolution_price`
- [ ] DuckDB-схема в `src/storage/schema.sql`
- [ ] Loader: `data/raw/*.parquet` → DuckDB-таблица `markets`
- [ ] Бюджет запросов: `/markets` лимит 300 req/10s → бьёмся в 200 req/10s для запаса. На 50k рынков с пагинацией по 500 = ~100 запросов, 5–10 секунд работы

### Артефакты

- `src/collectors/gamma_markets.py` — сборщик
- `src/storage/duckdb_loader.py` — parquet → duckdb
- `data/raw/markets_<YYYY-MM-DD>.parquet` — сырой дамп
- `data/processed/gadalka.duckdb` — рабочая БД
- `notebooks/02_first_dataset_look.ipynb` — что мы скачали

### Критерии завершения дня

- ✅ Скачано все резолвнутые рынки (target: ≥10k)
- ✅ DuckDB-таблица грузится, SQL-запросы работают
- ✅ Понятно, какие поля иногда NULL, какие всегда заполнены

---

## 📅 День 4 — Price history feasibility check ⚠️ КРИТИЧНЫЙ ДЕНЬ

Без price-history до резолва **бэктест построить нельзя**. Этот день решает, идём ли мы вообще дальше.

### Задачи

- [ ] **Источник 1: Polymarket TimeSeries `/prices-history`**
  - Для выборки из 20 случайных закрытых рынков → запросить историю с интервалами `1m`, `1h`, `6h`
  - Понять: какая максимальная глубина? Какая гранулярность?
  - Замерить: для рынка с volume <$10k — есть ли история, или только для топов?
- [ ] **Источник 2: On-chain Polygon RPC**
  - Найти адрес контракта CTF Exchange
  - Через бесплатный RPC (Alchemy free / Chainstack / public) запросить event logs `OrderFilled` для одного condition_id
  - Прикинуть стоимость и время для full reconstruction price-curve по 20 рынкам
- [ ] **Источник 3: Dune Analytics**
  - Поискать community queries на `polymarket`, `polymarket_polygon`
  - Проверить наличие готовых price snapshots
- [ ] **Источник 4: pm.wiki, polymarket-analytics, сторонние агрегаторы** (из Day 1 каталога)
  - Быстрая проверка, есть ли публичные dumps

### Критерии успеха

Должна быть возможность для **≥500 рынков** получить price snapshot за **T-1h, T-6h, T-24h, T-7d до резолва** одним из источников. Желательно — комбинируя.

### Артефакты

- `notebooks/03_price_history_feasibility.ipynb` — все эксперименты с источниками
- `docs/price-history-sources.md` — вердикт: какой источник используем, плюсы/минусы
- `src/collectors/price_history.py` — прототип сборщика для выбранного источника

### 🚨 Gate: go / no-go

- ✅ Нашли источник pre-resolution price на ≥500 рынков → **GO** в День 5
- ⚠️ Только для топ-100 рынков → **PARTIAL GO** — ограничиваемся high-volume категориями
- ❌ Никак нельзя получить historical prices → **STOP**, обсуждаем pivot

---

## 📅 День 5 — EDA на закрытых рынках

### Задачи

- [ ] EDA в `notebooks/04_eda.ipynb`:
  - Распределение по категориям (Politics, Crypto, Sports, Science, etc.)
  - Гистограмма volume_total, liquidity → где толстый хвост, где длинный
  - Hit rate `YES` vs `NO` outcomes (есть ли bias датасета?)
  - Распределение времени жизни рынка
  - Распределение **финальной цены за T-1h до резолва** (favorite-longshot histogram)
  - Корреляция volume vs точность рынка (правда ли что high-volume рынки точнее?)
- [ ] **Анализ longshot-гипотезы** (отдельно):
  - Возьми все случаи где цена за T-24h была <$0.10
  - Сколько резолвнулось `YES`? Какой средний выигрыш?
  - То же для порогов $0.05, $0.15, $0.20
  - **Это первичный sanity check** longshot-стратегии до полного бэктеста
- [ ] **Анализ favorite-bias:**
  - Цена ≥$0.90 за T-24h: какой % резолвится `NO`?
- [ ] Описать **3 candidate edge-гипотезы** для Фазы 1

### Артефакты

- `notebooks/04_eda.ipynb` — графики и таблицы
- `plans/phase-0-findings.md` — выводы: статистика, longshot sanity check, candidate hypotheses

### Критерии завершения

- ✅ Распределения понятны
- ✅ Longshot sanity check: есть ли наивный позитивный EV или нет
- ✅ Сформулированы 3 гипотезы для Фазы 1 с предполагаемым размером EV

---

## 📅 День 6 — Go/no-go и план Фазы 1

### Задачи

- [ ] Свести всё в итоговый отчёт `plans/phase-0-findings.md`
- [ ] Написать `plans/phase-1-backtest.md` — детальный план Фазы 1
- [ ] **Решение go/no-go** (мы вдвоём): идём в Фазу 1 или нет

### Артефакты

- `plans/phase-0-findings.md` — финальные данные Фазы 0
- `plans/phase-1-backtest.md` — план следующей фазы (на верификацию)

---

## 🚧 Риски и митигации

| Риск | Вероятность | Импакт | Митигация |
|------|-------------|--------|-----------|
| Geo-блок read API из РФ | Низкая | Высокий | Day 2 проверка; если блок — VPN на собственном VPS |
| Pre-resolution price недоступна | Средняя | Критичный | Day 4 — fallback через on-chain Polygon |
| Survivorship bias в metadata | Высокая | Средний | Day 5 — explicitly смотреть только на закрытые-резолвнутые, не отменённые |
| Look-ahead bias (поля обновляются post-resolution) | Средняя | Высокий | Day 2 — внимательно смотреть какие поля могут меняться после end_date |
| Polymarket меняет API без уведомления | Низкая | Средний | Сохраняем сырые ответы в parquet, чтобы можно было пере-парсить |
| Очень мало данных в нужной категории | Средняя | Высокий | Не фиксируем категорию заранее, выбираем по факту из EDA |
| Edge уже вытоптан конкурентами | Высокая | Высокий | Day 1 landscape research — явно ищем «забитые» зоны и обходим |
| Изобретаем велосипед | Средняя | Низкий | Day 1 — оценить SDK/готовые компоненты до написания своего |

## 📊 Известные параметры (для контекста)

### Rate limits Polymarket API

| API | Общий | Узкие места |
|-----|-------|-------------|
| Gamma | 4000 req / 10s | `/events` 500/10s, `/markets` 300/10s, search 350/10s |
| CLOB | 9000 req / 10s | `/book` `/price` `/midpoint` 1500/10s; batch 500/10s |
| Data | 1000 req / 10s | `/trades` 200/10s, `/positions` 150/10s |

Глобальный потолок Cloudflare: 15k req/10s.
429 → exponential backoff + jitter.

### Auth

- Read endpoints (Gamma, CLOB market data, Data API) — **public, без auth**.
- Только запись ордеров требует HMAC + EIP-712 signing. В Фазе 0 не нужно.

### Юрисдикция

- Polymarket официально blocked for US persons (CFTC settlement 2022).
- Read API доступен глобально, но **trade-flow требует non-US wallet**.
- Для РФ: торговля не запрещена явно. Доступ к UI / API — проверяем в Day 2.

---

## ✅ Чек-лист завершения Фазы 0

- [ ] Day 1: APIs landscape + existing projects map готовы
- [ ] Day 2: API exploration done, схемы зафиксированы
- [ ] Day 3: скачан полный dataset закрытых рынков (≥10k)
- [ ] Day 4: решена feasibility price-history (Gate)
- [ ] Day 5: сделан EDA, longshot sanity check выполнен
- [ ] Day 6: сформулированы 3 candidate edge-гипотезы для Фазы 1
- [ ] Написан `plans/phase-1-backtest.md`
- [ ] Принято go/no-go решение

---

## 🔄 Журнал изменений плана

| Дата | Изменение | Автор |
|------|-----------|-------|
| 2026-05-23 | Первая версия плана, статус: на верификации | Claude |
| 2026-05-23 | Добавлен Day 1 — Landscape research (APIs ecosystem + existing projects). Срок 3–5 → 4–6 дней. Остальные дни сдвинуты на +1. | Claude (по запросу пользователя) |
