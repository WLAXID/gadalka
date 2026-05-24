# 🔬 Phase 2 Patch — Широкий бэктест

**Срок:** 1-2 дня (написание + прогон)
**Капитал:** $0 (research)
**Статус:** 🟡 На верификации пользователя

---

## 🎯 Зачем

24.05.2026 после правки пагинации (см. [`feat(scan): пагинируем все активные рынки`](../src/paper/signal.py)) live-сканер выдал **546 сигналов за один проход** вместо ожидаемых 1-2. Корень — `max_market_ttl_days=60` пускал в выборку рынки с резолвом за 2 месяца, тогда как Phase 1 backtest валидировал edge только на T-1h..T-7d.

Прежде чем чинить — **нужно знать цифры**, а не угадывать пороги. Текущий бэктест Phase 1 (`scripts/run_backtest.py`) проверял **8 фиксированных конфигов** и не разрезал по горизонту/volume/времени. Делаем **широкий** прогон, чтобы ответить разом на 4 вопроса:

1. На каком entry_horizon edge ещё жив? (T-1h..T-30d sweep)
2. Работает ли edge на low-volume рынках или нужен `min_volume`?
3. Что будет фактически делать paper-trader каждый день в проде?
4. Есть ли лучшие H1-варианты, которые мы пропустили?

## 🚫 Что НЕ делаем

- ❌ Не трогаем live-код до конца бэктеста — контейнер остановлен, 546 ставок никуда не денутся.
- ❌ Не дорабатываем ML-модели — H2 logistic regression уже валидирован в Phase 1.
- ❌ Не подкручиваем sizing (Kelly и пр.) — преждевременно.
- ❌ Не лезем в новый dataset pull — работаем с тем что есть (12.4M prices, 10100 markets).

---

## 📐 Архитектура: 4 параллельных среза

Один скрипт `scripts/run_wide_backtest.py`. Все срезы пишутся в `data/wide_backtest/`, каждый — отдельный parquet + summary CSV. Финальный отчёт собирается в Jupyter notebook `notebooks/wide_backtest_report.ipynb`.

### Срез A — Horizon sweep (главный)

Цель: найти как меняется EV при удалении окна входа от резолва.

**Параметры:**
- Стратегия: `BuyFavoriteStrategy(low=0.50, high=0.85)` — baseline H1
- Горизонты: `T-1h, T-3h, T-6h, T-12h, T-24h, T-2d, T-3d, T-5d, T-7d, T-14d, T-30d, T-60d, T-90d`
- Costs: `realistic`
- Volume фильтр: none

**Что нужно для T-h≠24:** в `backtest/dataset.py` есть функция выбора цены на T-X — расширить с дискретного `t24h` на любое `t<seconds>`. Использовать `prices` parquet, бинарный поиск по `timestamp <= end_ts - h*3600`.

**Output:** таблица `horizon → n_trades, ev_gross, ev_net, sharpe, win_rate, max_dd`.

**Acceptance:** видим где EV переходит в отрицательную зону / Sharpe падает ниже 1.0. Это даёт чёткий cutoff для `entry_horizon_days` в live.

### Срез B — Volume buckets × horizon

Цель: понять есть ли edge на low-volume рынках, или их нужно резать.

**Параметры:**
- Стратегия: baseline H1
- Volume buckets: `<$1k, $1k-10k, $10k-100k, $100k-1M, >$1M` (5 buckets из EDA, см. `scripts/eda_dataset.py:271`)
- Горизонты: подмножество из A — `T-3h, T-24h, T-3d, T-7d`
- Costs: `realistic` + `pessimistic`

**Output:** heatmap `volume_bucket × horizon → ev_net, n_trades`.

**Acceptance:** видим — нужен ли `min_volume` фильтр и какой именно ($1k vs $10k).

### Срез C — Live-style simulation (самое честное)

Цель: воспроизвести **временну́ю концентрацию** ставок которую увидел paper-trader.

**Логика:**
- Для каждого исторического дня `d` в датасете (~365 дней):
  - Найти все рынки, где `end_date ∈ [d, d + entry_horizon_days]` И `price_yes на момент d ∈ [0.50, 0.85]` И `volume_на_момент_d >= min_volume`
  - Открыть paper-ставку $1 на каждом таком рынке
  - Учесть резолв когда придёт
- Считать **дневную** статистику: trades/day, exposure/day, PnL/day

**Параметры:**
- `entry_horizon_days ∈ {1, 3, 7, 14}`
- `min_volume ∈ {0, 1000, 10000}`
- Costs: `realistic`

**Output:**
- Timeline plot: trades_per_day, exposure_per_day, cumulative_pnl
- Distribution: max_trades_in_one_day, p99_exposure
- Сравнение с paper-инцидентом (546 trades в момент)

**Acceptance:**
- Подтверждаем что выбранный `entry_horizon` даёт ~разумное число trades/day (target: median 1-5, p99 < 30).
- Видим что cumulative PnL положительный и Sharpe > 1.
- Понимаем какой capital нужен в обороте (max concurrent pending × $1).

### Срез D — Parametric grid

Цель: найти sweet spot по price band если он есть.

**Параметры:**
- `low ∈ {0.45, 0.50, 0.55, 0.60, 0.65, 0.70}`
- `high ∈ {0.80, 0.85, 0.90, 0.95}`
- Горизонт: best из A (фиксируется по результатам)
- Volume: best из B
- Costs: `realistic`

**Output:** heatmap `low × high → ev_net, n_trades`.

**Acceptance:** видим оптимум. Если он сильно отличается от текущих `[0.50, 0.85]` — пересматриваем live-параметры.

---

## 🧮 Метрики (все срезы)

Для каждой ячейки:
- `n_trades` — сколько ставок открылось
- `ev_net` — средний PnL за ставку после fees+spread+slippage
- `total_pnl` — кумулятив
- `win_rate` — доля выигрышных
- `sharpe` — sqrt(252) × mean/std дневных PnL (для срезов с временно́й структурой)
- `max_dd` — макс. просадка кумулятивного PnL
- `avg_volume`, `median_volume` — sanity-check выборки

## 📦 Что нужно в коде

### Новый файл: `scripts/run_wide_backtest.py`
- 4 функции — по одной на срез
- Каждая пишет parquet в `data/wide_backtest/{slice}_results.parquet`
- Финальный stdout-summary с топ-результатами

### Правки в `src/backtest/`:
- `dataset.py`: функция `price_at_horizon(market, hours_before_end)` — общая, не дискретная по `t24h/t7d`
- `strategies.py`: добавить параметр `horizon_hours` (заменяет/дополняет `horizon: str`)
- `engine.py`: если что-то нужно для live-style симуляции — посмотрим в процессе

### Новый: `notebooks/wide_backtest_report.ipynb`
- Загружает все 4 parquet'а
- Рисует таблицы и heatmaps
- Заканчивается секцией **«Решения для live»** с конкретными числами

## ⏱ Прогноз времени и стоимости

- Код: ~3-4 часа (главная сложность — переписать `price_at_horizon` универсально)
- Прогон всех 4 срезов: 1-3 часа на полном датасете (10k markets × ~50 конфигов)
- Notebook + findings: 1-2 часа
- **Итого: 1 рабочий день**

API-запросов: 0. Всё из локального parquet.

## 🎯 Что станет понятно после

После прогона ты получишь:
1. **Конкретное число** для `entry_horizon_days` в live (вместо 60 или 7 наугад)
2. **Конкретное число** для `min_market_volume` (или подтверждение что 0 — норм)
3. **Distribution trades/day** — чтобы знать чего ожидать в paper и какой capital держать
4. **Подтверждение или отказ** от текущих границ `[0.50, 0.85]`
5. **Файл `phase-2-wide-backtest-findings.md`** с конкретными рекомендациями для правки `signal.py`

## 🔗 Параллельные треки

**Инцидент с 546 ставками** остаётся открытым. Контейнер `gadalka-paper` остановлен, БД зафиксирована. Откатить/пометить эти ставки можно:
- (A) После бэктеста — если найдём что они на самом деле не катастрофа
- (B) Сейчас — пометить status='invalid' одним UPDATE с фильтром `opened_at >= 1716539340` (08:29 UTC)

Рекомендую **(A)**: бэктест может показать, что часть из 546 на самом деле в полосе с положительным EV — тогда оставить их даст реальные данные для сравнения live vs backtest.

---

## ✅ Запрос на верификацию

1. Список срезов A-D — всё или что-то лишнее/недостающее?
2. Стек: я планирую DuckDB + polars для агрегаций (быстрее pandas на 10k markets × 12M prices). Норм?
3. `notebooks/wide_backtest_report.ipynb` или обычный markdown findings? У нас есть jupyter в окружении?
4. По 546 ставкам: подтверждаешь план (A) — оставить пока, разбираться после бэктеста?
