# 🔍 KeyPool probe — финальные результаты

**Дата:** 2026-05-23
**Скрипт:** `scripts/probe_keys.py`
**Raw:** `data/cache/probe_results.json`

---

## TL;DR — что в пуле реально

### 🎉 Главные победы

1. **Polygon.io: ВСЕ 40 ключей — Crypto Starter+** ($49/mo каждый)
   - Минутная гранулярность BTC/ETH ✅
   - 3+ года истории ✅
   - WebSocket стриминг доступен ✅
   - **Это game-changer для крипто-категории Polymarket.** Раньше эту фичу мы оценивали в $49/mo paid tier — у нас её **40× free**

2. **Alchemy: 87/115 ключей с archive на Polygon mainnet** ✅
   - Archive node для исторических event logs CTF Exchange
   - Free tier: 30M CU/мес → пул = **2.6B CU/мес**
   - Главный fallback price-history полностью покрыт

3. **CoinGecko: 66/67 ключей рабочие (Demo)** ✅
   - 10k calls/мес каждый → пул = **660k calls/мес**
   - Резерв для крипто-цен, если Polygon.io не покроет какой-то актив

### ⚠️ Не game-changer, но рабочее

- **NewsAPI: 49/49 — все Developer** (история только 1 мес). **Business-ключей в пуле НЕТ**. Для historical news → переключаемся на GDELT BigQuery (бесплатно, 8B+ статей).
- **Tavily: 1/8 Pro+ с Extract API**. Остальные 7 за квотой. Экономим этот 1 ключ.
- **Perplexity: 1/1 sonar online OK**
- **Chainstack: 5/9 рабочих** (3 с archive). Backup к Alchemy.
- **The Odds API: 18/18, 8 со свежей квотой 500/500**

### 🔻 Не используем активно

- **DeepSeek:** 3/4 рабочие, но 2 — отрицательный/нулевой баланс. Один за $0 USD пустой.
- **OpenAI: 2/12 рабочих** с gpt-4 (10 timeout). 2 ключа хватит на изредка-LLM-задачи.
- **FRED: 26/26 ok** — free, для экономических данных США (politics-рынки).

---

## Полная таблица

| Провайдер | Всего | Работают | Платные фичи | Заметка |
|-----------|-------|----------|--------------|---------|
| polygon | 40 | **40 (100%)** | crypto_minute_aggs ⭐ | Все Crypto Starter+ |
| alchemy | 115 | **87 (76%)** | archive ⭐ | 21 без MATIC доступа, 7 за квотой |
| coingecko | 67 | 66 (99%) | — | Demo free, всего х10k/мес |
| newsapi | 49 | 49 (100%) | — | Все Developer 1 мес |
| fred | 26 | 26 (100%) | — | Free |
| odds_api | 18 | 18 (100%) | — | 8 свежих, 10 частично использованы |
| openai | 12 | 2 (17%) | gpt4_access | 10 timeout |
| chainstack | 9 | 5 (56%) | archive | 3 с archive, 2 dev, 4 битых |
| tavily | 8 | 1 (12%) | extract | 7 за квотой |
| deepseek | 4 | 3 (75%) | — | 2 с отриц. балансом |
| perplexity | 1 | 1 (100%) | online_search | OK |

**Итого:** 273 рабочих ключа из 359 пробуренных по 11 core/useful провайдерам.

---

## Что это меняет для gadalka

### Полностью открылось

1. **Крипто-категория Polymarket** — со 40 ключами Polygon.io можно:
   - Получать минутные OHLCV BTC/ETH за 3+ года
   - Реал-тайм WebSocket для live-стратегий в Фазе 2+
   - Reference price feed для резолва крипто-рынков
   - **Гипотеза «mid-volume крипто mispricing» получает мощный feed**

2. **On-chain price-history** через Alchemy archive node
   - CTF Exchange event logs за всю историю Polymarket
   - 2.6B CU/мес — больше чем понадобится для всего бэктеста
   - Можно реконструировать orderbook на любой момент

### Закрылось / переключаем

1. **Historical news через NewsAPI** — нет Business в пуле, 1 мес истории недостаточно для бэктеста.
   - **Переключаемся на GDELT BigQuery** — 8B+ статей с 2015, бесплатно. Это даже лучше: GDELT даёт tone, entity extraction, geo tags из коробки.

2. **News-augmented LLM** — Perplexity 1 ключ нужно экономить, OpenAI 2 ключа.
   - **Для массовой обработки** — пополнить DeepSeek баланс ($5–10) или использовать локальный Ollama для baseline-классификации.

3. **Tavily** — 1 ключ с extract беречь для критичных операций.

---

## План по обновлению стратегии

### Гипотезы Фазы 1 — переупорядочиваем по приоритету

1. ⭐⭐⭐ **Crypto mid-volume mispricing** — мощный data stack (Polygon.io + on-chain через Alchemy). **Главный кандидат.**
2. ⭐⭐ **News-lag в нишах** — переключаемся на **GDELT** для исторических данных вместо NewsAPI
3. ⭐⭐ **Combinatorial arbitrage** — не зависит от наших API, работает на one Polymarket
4. ⭐ **Patient market-making mid-vol** — нужен real-time, можем строить через Polygon.io WS
5. (downgraded) **Longshot bias** — sanity check, не приоритет

### Day 2-4 — конкретные действия

- **Day 2:** клиент Polymarket Gamma/CLOB/Data; параллельно — клиент Polygon.io с ротацией 40 ключей
- **Day 3:** сборщик metadata Polymarket + сборщик минутных свечей Polygon.io для крипто-тикеров, релевантных нашим рынкам
- **Day 4:** Dune Analytics для historical Polymarket prices; Alchemy fallback через CTF Exchange logs; **GDELT для news**

---

## Технические заметки по probe

- 28 Alchemy ключей не имеют MATIC_MAINNET доступа (другие приложения у друга) — можно использовать для других сетей если понадобится
- 7 Alchemy ключей за месячной квотой — обновятся 1 числа
- 10 OpenAI ключей в ConnectTimeout — возможно geo-blocked или dead. Не критично, у нас 2 рабочих
- 8 Tavily за квотой — у этих ключей друзья сами выгребли
- 1 DeepSeek ключ с -$1.77 USD — отрицательный, надо пополнить или забыть

## Артефакты

- `scripts/probe_keys.py` — переиспользуемый probe, можно запускать раз в месяц
- `data/cache/probe_results.json` — raw результаты со всех ключей
- Этот документ — выводы и план

---

## Журнал

| Дата | Изменение |
|------|-----------|
| 2026-05-23 | Первый probe-run, 359 ключей по 11 провайдерам, 273 рабочих |
