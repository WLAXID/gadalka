# 🧪 Фаза 1 — Backtest

**Срок:** 5–7 дней
**Капитал:** $0 (research-only)
**Статус:** 🟡 На верификации пользователя

---

## 🎯 Цели фазы

1. Подтвердить или опровергнуть **H1 «Buy Favorite $0.50–$0.85»** на полноценном **walk-forward OOS** бэктесте.
2. Замерить **net EV, Sharpe, max DD** с реалистичными издержками (спред + slippage + fees).
3. Сравнить **H1** с ML-baseline (logistic regression) — даёт ли модель дополнительный edge.
4. Выработать **порог входа** (cut-off для signal) и **sizing rule** (Kelly fractional).
5. **Go/no-go в Фазу 2 (paper)** на основе чистых OOS-метрик.

## 🚫 Что НЕ делаем в Фазе 1

- ❌ Не пишем live-бот (это Фаза 2 → paper)
- ❌ Не подключаем кошелёк
- ❌ Не строим UI / dashboard
- ❌ Не торгуем on-chain
- ❌ Не тестируем гипотезы которые Day 5 уже опроверг (longshot, buy NO when fav)

## 📊 Гипотезы (порядок по приоритету)

### H1 — Buy Favorite ⭐ (главная)

- **Signal:** `price_yes_t24h ∈ [0.50, 0.85]`
- **Entry:** покупка YES по ask в T-24h
- **Exit:** держим до резолва
- **Baseline EV:** +12.7% per bet (после 2% fee, до spread)
- **Sample:** 1,148 markets с покрытием T-24h

### H1.a — Mid-vol filter
- Те же критерии + `volume ∈ [$10k, $100k]`
- Baseline EV на mid-vol $0.70-0.85: +17.5%, но n=58 (тонкая)

### H1.b — Tight price band
- `price_yes_t24h ∈ [0.65, 0.85]` (отрезаем колеблющуюся середину)

### H1.c — Multi-horizon ensemble
- Вход только если signal стабилен И в T-7d, И в T-24h (stability filter)

### H2 — Logistic regression baseline
- Features: `price_yes_t24h`, `volume`, `liquidity`, `time_to_resolve`, `category` (нужно тянуть events), `price_drift_t7d_to_t24h`, `n_history_points`
- Target: `resolved_yes`
- Сравнить с H1 на одной OOS-выборке

### H3 — В бэклог (если останется время)
- News-lag через GDELT
- Combinatorial arb между связанными рынками

## 📅 Day-by-day

### Day 1 — Backtest engine + train/test split

**Задачи:**
- [ ] `src/backtest/engine.py` — простой engine: positions table, mark-to-market, fees, slippage
- [ ] `src/backtest/strategies.py` — `BuyFavoriteStrategy(low, high)` интерфейс
- [ ] Walk-forward split:
  - Train: markets закрытые до 2026-03-31
  - Test: 2026-04-01 — 2026-05-23
- [ ] Реалистичные costs:
  - Spread: оценить из реальных bids/asks (mid + 1%? нужно посчитать на нашем data/raw/samples/clob_book.json)
  - Fee: 2% линейно от profit
  - Slippage: 0.5% при размере позиции <$100

**Артефакты:**
- `src/backtest/engine.py`
- `notebooks/05_backtest_baseline.ipynb`

### Day 2 — H1 базовый backtest

**Задачи:**
- [ ] Запустить H1 на train (~5000 markets)
- [ ] Замерить: P&L, win rate, EV per bet, Sharpe, max DD
- [ ] Sensitivity к параметрам `low/high` (grid search)
- [ ] Equity curve, distribution of trade outcomes

**Gate:** OOS net EV > +5% → продолжаем варианты. < 0% → STOP, обсуждаем pivot.

### Day 3 — H1.a / H1.b / H1.c варианты

- [ ] Mid-vol filter
- [ ] Tight band [0.65, 0.85]
- [ ] Multi-horizon ensemble
- [ ] Сравнение метрик: какой вариант даёт лучший Sharpe

### Day 4 — H2 ML baseline (logistic regression)

- [ ] Feature engineering на market table
- [ ] Train logistic regression на train-set
- [ ] Predict probability на test, сравнить с рыночной ценой
- [ ] Стратегия: купить YES если P_model(yes) - price_yes_t24h > threshold
- [ ] Метрики vs H1 baseline

### Day 5 — Walk-forward OOS (rolling window)

- [ ] Rolling train-test (например, train на T-90 дней до каждого момента, test на T+30 дней)
- [ ] Confirm stability of edge over time
- [ ] Distribution of monthly returns

### Day 6 — Sizing + risk

- [ ] Kelly fractional: `f = edge / variance` × 0.25 (conservative)
- [ ] Симуляция с реальным sizing
- [ ] Stress test: что если win rate упадёт на 5pp? DD?

### Day 7 — Финальные выводы

- [ ] `plans/phase-1-findings.md`
- [ ] `plans/phase-2-paper.md` (если GO)
- [ ] Go/no-go решение

## 🚨 Critical gates

| Gate | Условие | Действие если не выполнено |
|------|---------|----------------------------|
| Day 2 | OOS net EV > +5% после fees+spread | STOP, обсудить pivot или отказ |
| Day 5 | Edge стабилен в rolling OOS (≥3 из 4 окон с EV > 0) | STOP, edge может быть случайным |
| Day 6 | Max DD < 30% с Kelly/4 sizing | Уменьшить размер или отказаться |
| Day 7 | Sharpe > 1.0 | Подумать — стоит ли вообще |

## 📋 Артефакты

- `src/backtest/engine.py` — engine
- `src/backtest/strategies.py` — стратегии (H1, H1.a, H1.b, H1.c, H2)
- `src/backtest/costs.py` — модель издержек
- `notebooks/05_backtest_baseline.ipynb` — H1
- `notebooks/06_walk_forward.ipynb` — OOS
- `notebooks/07_ml_baseline.ipynb` — H2
- `plans/phase-1-findings.md` — финальный отчёт

## 🚧 Риски

| Риск | Митигация |
|------|-----------|
| Edge — survivorship bias (32% 2025 рынков missing) | Walk-forward с rolling windows; check edge на 2026-only |
| Spread > 2% на mid-vol → eats EV | Замерить реальный спред из bids/asks нашего датасета; conservative slippage |
| Look-ahead bias в features | Все feature compute только на data до T-24h |
| Overfitting parameters | Grid search ТОЛЬКО на train, test единожды |
| Variance высокая | Sharpe + DD analysis; sizing через Kelly |

## ⚠️ OFAC-блокер (напоминание)

Фаза 1 = research, без trading. РФ-блок не влияет.
В Фазе 3 (pilot $100) → решение pivot/обход/paper-only.

## 📝 Что нужно от пользователя

1. Подтвердить дизайн walk-forward (3-mo train / 1-mo test? rolling?)
2. Подтвердить размер ставок в симуляции (Kelly fractional 1/4? фиксированные $5?)
3. Подтвердить готовность к Day 4 ML — есть ли блокеры (категория? нужно ли тянуть events?)
4. Время — 5–6 дней реалистично?

---

## Журнал

| Дата | Изменение |
|------|-----------|
| 2026-05-23 | Первая версия после Day 5 EDA. Главная гипотеза H1 «Buy Favorite». |
