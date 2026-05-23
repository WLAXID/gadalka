# 📋 Phase 0 — Day 3 (+ Day 4 объединены) Findings

**Дата:** 2026-05-23
**Артефакты:** `data/raw/markets_2026-05-23.parquet`, `data/raw/prices_history/batch_*.parquet`, `data/processed/gadalka.duckdb`

---

## TL;DR

**Дataset готов. 10100 закрытых рынков + 12.4M точек цен за 7 минут pull.**

| Метрика | Значение |
|---------|----------|
| Закрытых рынков (metadata) | **10,100** |
| Точек prices-history | **12,414,608** |
| Рынков с историей | **6,847 (68%)** |
| Уникальных token_id с историей | **13,694** |
| Время full pull (Gamma + CLOB) | **7 минут** |

---

## 1. Что собрали

### Markets metadata
- 10,100 закрытых рынков через `/markets?closed=true` (offset-pagination)
- **Лимит Polymarket Gamma: offset ≤ 10000.** Hard cap, дальше 422. Все наши = свежайшие закрытые.
- Полные поля: `id`, `conditionId`, `slug`, `question`, `endDate`, `volumeNum`, `liquidity`, `outcomes`, `outcomePrices`, `clobTokenIds`, ...
- Парсенные derivatives: `token_id_yes`, `token_id_no`, `final_price_yes`, `final_price_no`, `resolved_yes`

### Prices history
- 12.4M точек через `/clob/prices-history?interval=max`
- Long-формат: `(condition_id, token_id, outcome, t, p)`
- Полный исторический рейтинг бэктеста для 6847 рынков

---

## 2. Распределение по объёму

| Volume bucket | Markets |
|---------------|---------|
| > $1M | 297 |
| $100k – $1M | 906 |
| $10k – $100k | 1,438 ← **Mid-volume sweet spot** |
| $1k – $10k | 1,758 |
| $100 – $1k | 2,209 |
| < $100 | 1,535 |
| NULL volume | 1,957 |

> Для main edge-гипотезы (mid-volume mispricing) у нас **1,438 рынков в зоне $10k–$100k**.

## 3. Резолв-исходы

| | Count |
|---|---|
| Resolved YES | 3,721 (36.8%) |
| Resolved NO | 6,379 (63.2%) |
| Unknown | 0 |

Heavy bias к NO — это **подтверждает Reichenbach & Walther 2026**: на Polymarket большинство «Will X happen?» вопросов резолвится NO. Longshot bias **обратный** обычному.

## 4. Топ-10 рынков по объёму

1. **Russia × Ukraine ceasefire by May 31, 2026** — $141M, YES ✅
2. Will Trump nominate Judy Shelton as Fed chair — $127M, NO
3. Chelsea 2025–26 EPL win — $91M, NO
4. Russia × Ukraine ceasefire by June 30, 2026 — $60M, YES ✅
5. Trump nominate Kevin Warsh as Fed chair — $59M, YES ✅
6-10: Trump Fed chair nominations, EPL, NBA finals

Полно политики (Trump nominations) и спорта.

## 5. ⚠️ Покрытие prices-history — критичная находка

**Только 68% рынков отдают историю через CLOB. Это retention issue.**

| Год создания | Рынков | С историей | Покрытие |
|--------------|--------|------------|----------|
| 2026 | 8080 | 6196 | **76.7%** ✅ |
| 2025 | 2020 | 651 | **32.2%** ⚠️ |

**Вывод:**
- CLOB TimeSeries имеет ~12 мес retention. Старые рынки теряются.
- 3,253 рынков без истории, среди них **1,539 с volume > $10k** — это потеря «жирных» данных.
- Earliest createdAt = 2025-01-05, latest = 2026-04-21 (среди missing).

**Для Фазы 1 (бэктест):** 6,847 рынков с историей — достаточно для baseline-модели. Не нужно гнаться за 100% сразу.

**Для расширения dataset на 2025 (важно для большего N):** нужен **on-chain Alchemy fallback** — парсим event logs CTF Exchange Polygon-контракта. У нас 87 ключей с archive node = 2.6B CU/мес — хватит на много раз пересобрать историю.

## 6. Что это значит для стратегии

### Сейчас (Фаза 1 — бэктест)
- **Hypothesis #1 (mid-volume mispricing):** 1438 рынков в $10k–$100k, бóльшая часть с историей. Достаточно для baseline-модели и OOS-валидации.
- **Hypothesis longshot bias:** на нашем датасете 36.8% YES → можем явно проверить, работает ли «купить NO дёшево» (наивная longshot стратегия).
- **News-lag, combinatorial arb** — требуют доп. данных (NewsAPI/GDELT + связи между рынками через events).

### В Фазе 1 если нужно больше данных
- On-chain Alchemy fallback — реализуем когда поймём, чего конкретно не хватает (а не «на всякий случай»)
- Period-shift: подождать месяц-два и пересобрать — за 7 минут это не проблема

---

## 7. Технические заметки

### Что узнали по API сегодня
- **Polymarket Gamma offset hard-capped на 10,000** — для большего нужна разбивка через `condition_ids` или фильтры по дате
- **`/markets?limit=>100` режется до 100** — все ответы имеют максимум 100 markets
- **Gamma /events содержит category, /markets не содержит** — для категориальной разбивки нужно тянуть события (Day 5 EDA)
- **CLOB prices-history `interval=max`** даёт всю историю **только для свежих рынков** (≤12 мес retention)

### Архитектура
- `src/collectors/gamma_markets.py` → `MarketsCollector.collect_all_closed()`
- `src/collectors/prices_history.py` → `PricesHistoryCollector.collect_for_tokens()` (concurrency=7)
- `src/storage/duckdb_loader.py` → `GadalkaDB.register_parquet_views()`
- `scripts/collect_dataset.py` — orchestrator (max-pages, skip-prices флаги)
- `scripts/sanity_check_dataset.py` — диагностика

### Скорости
- Markets pagination: ~70 страниц/сек → 10k markets за 1.5 сек
- Prices history: 47–51 req/s с concurrency=7 → 20k token-историй за 7 мин
- Bottleneck: задержка CLOB ~0.12s per request

---

## 8. Чек-лист Day 3 (объединён с Day 4)

- [x] MarketsCollector с offset-pagination, handling Gamma offset cap
- [x] PricesHistoryCollector с concurrency=7
- [x] Parquet writer per-batch (resumable)
- [x] DuckDB loader через parquet views
- [x] Sanity-check скрипт с диагностикой покрытия
- [x] Зафиксирована retention CLOB TimeSeries (~12 мес)
- [x] Gate Day 4: **GO в Фазу 1** — 6847 рынков с историей достаточно для baseline

---

## 9. Что в Day 5 (EDA + candidate hypotheses)

- Подтянуть events для category-разбивки
- EDA-распределения: время жизни рынков, спред в момент резолва, корреляция volume vs точность
- **Longshot sanity check:** на наших 6847 — стратегия «купить NO с ценой <$0.10 за T-24h до резолва» — что бы выдала?
- **Favorite check:** рынки с ценой ≥$0.90 за T-24h — % резолва NO?
- Сформулировать **3 финальные гипотезы для Фазы 1** с numbers
- Решение **go/no-go в Фазу 1**

---

## Журнал

| Дата | Изменение |
|------|-----------|
| 2026-05-23 | Первый полный pull: 10,100 markets + 12.4M prices, 7 мин |
