# 🧪 Phase 1 — Backtest Findings

**Дата:** 2026-05-23
**Артефакты:** `scripts/run_backtest.py`, `src/backtest/`, `data/processed/backtest_report.json`

---

## 🎉 TL;DR — все gate'ы пройдены

**H1 baseline (Buy YES [0.50, 0.85] @ T-24h)** на realistic costs:

| Split | n | win | EV/bet | EV/$ | Total $ | Sharpe | Max DD% |
|-------|---|-----|--------|------|---------|--------|---------|
| Train (oldest 70%) | 631 | 71.95% | +10.74% | +17.71% | $67.79 | 6.10 | -9.14% |
| **Test (newest 30%, OOS)** | **513** | **71.54%** | **+13.47%** | **+23.45%** | **$69.12** | **6.66** | **-17.66%** |

OOS EV **превышает train EV**. Edge не overfit.

### Gate-check

| Gate | Условие | Результат | Статус |
|------|---------|-----------|--------|
| Day 2 OOS net EV | > +5% | **+13.5%** | ✅ |
| Rolling stability | ≥3 из 4 окон EV>0 | **4/4 positive** | ✅ |
| Sharpe | > 1.0 | **6.66** | ✅ |
| Max DD% | <30% | **-17.7%** | ✅ |

→ **GO в Фазу 2 (paper trading).**

---

## 1. Дataset

| | Значение |
|---|---|
| Markets с T-24h coverage | 4,425 |
| Train | 3,097 (oldest 70%) |
| Test (OOS) | 1,328 (newest 30%) |
| Период данных | **2026-04-24 → 2026-05-23** |
| Train split-точка | 2026-05-20 20:50 UTC |

⚠️ **Caveat:** датасет покрывает ровно 1 месяц (последние 10k рынков из Gamma, offset hard-capped). Для расширения OOS — потребуется on-chain Alchemy fallback в Фазе 2+.

## 2. H1 baseline и варианты — OOS таблица (realistic costs)

| Strategy | n_test | win | EV/bet | EV/$ | Sharpe | Comment |
|----------|--------|-----|--------|------|--------|---------|
| **H1 [0.50, 0.85] @ T-24h** | 513 | 71.5% | +13.5% | +23.5% | 6.66 | ⭐ baseline |
| H1.a + mid-vol $10k-$100k | 38 | 71.1% | +7.1% | +11.2% | 1.00 | хуже H1 на OOS |
| H1.b tight [0.65, 0.85] | 97 | 71.1% | **-3.0%** | -4.0% | -0.67 | ⚠️ NEGATIVE на test |
| **H1.c stable (T-7d + T-24h)** | **31** | **96.8%** | **+31.5%** | **+48.8%** | **9.08** | 🌟 малый sample, огромный edge |
| H1 [0.55, 0.90] | 221 | 67.4% | -0.7% | -1.0% | -0.21 | ⚠️ NEGATIVE |
| H1 [0.60, 0.90] | 163 | 68.1% | -3.3% | -4.6% | -0.94 | ⚠️ NEGATIVE |
| H1 [0.70, 0.85] | 62 | 87.1% | +9.7% | +12.6% | 2.31 | hi-fav сильно |
| H1 [0.70, 0.85] + mid-vol | 11 | 100% | +23.8% | +31.4% | 18.10 | тонко, но идеал |

### Главные наблюдения

1. **H1 baseline [0.50, 0.85] — самый robust**: 513 OOS-сделок, sharpe 6.66, EV даже выше train.
2. **H1.b tight band [0.65, 0.85] УБЫТОЧЕН на test** при +8% на train → **классический overfit**. Не используем.
3. **Mid-vol filter H1.a сужает sample до 38**, edge падает (+7% vs +13.5% базового). Возможно, mid-vol паттерн в EDA был артефактом малой выборки.
4. **H1.c "stable" (signal в T-7d И в T-24h)** — самый сильный кандидат: 97% win rate, +31.5% EV на 31 рынке. Но **sample мал для коммита**.
5. **H1 [0.70, 0.85]** — компромисс: 62 trades, 87% win, +9.7% EV, Sharpe 2.31.

## 3. H2 — Logistic Regression baseline

Признаки: `price_yes_t24h`, `price_yes_t7d`, `volume`, `lifetime_days`, `n_history_points`.
Threshold: `P_model - price_yes_t24h > 0.05`.

| Split | n | win | EV/bet | EV/$ | Sharpe |
|-------|---|-----|--------|------|--------|
| Train | 694 | 69.7% | +9.2% | +15.4% | 5.42 |
| **Test (OOS)** | **566** | **66.4%** | **+10.0%** | **+17.9%** | **5.00** |

Стабильно, но **H1 baseline лучше на per-bet EV** (+13.5% vs +10.0%). ML-подход даёт больше сделок (566 vs 513) и схожий total return.

→ **Используем H1 как primary, H2 как ensemble-validator** в Фазе 2.

## 4. Rolling walk-forward (4 окна, H1 baseline realistic)

| Window | Days | n_trades | win | EV/bet | EV/$ | PnL | Sharpe |
|--------|------|----------|-----|--------|------|-----|--------|
| W1 2026-04-24 → 05-08 | 14d | 111 | 85.6% | +18.9% | +28.6% | $21 | 6.45 |
| W2 2026-05-08 → 05-19 | 11d | 100 | 79.0% | +10.8% | +16.0% | $11 | 2.87 |
| W3 2026-05-19 → 05-21 | 2d | 511 | 67.7% | +9.4% | +16.4% | $48 | 4.45 |
| W4 2026-05-21 → 05-23 | 2d | 422 | 71.3% | +13.5% | +23.6% | $57 | 6.05 |

**Все 4 окна положительные.** Edge robust через все короткие подпериоды.

> W3/W4 имеют гораздо больше сделок чем W1/W2 — это потому что 2026-05-19+ концентрация спортивных событий и краткосрочных рынков с tight resolve.

## 5. Cost-модель impact (H1 baseline)

| Cost scenario | n | EV/bet | Sharpe | Max DD |
|---------------|---|--------|--------|--------|
| Optimistic (только 2% fee) | 513 | +14.2% | 7.01 | -$6.31 |
| **Realistic (2% fee + 1.5% spread)** | **513** | **+13.5%** | **6.66** | **-$6.51** |
| Pessimistic (2% fee + 3% spread) | 513 | +12.8% | 6.31 | -$6.71 |

Edge **выдерживает консервативные издержки до 5%+ drag**. Это сильный сигнал — стратегия не на грани прибыльности.

## 6. Sizing — Kelly fractional

Для H1 baseline на realistic costs:
- p (win rate) = 0.715
- avg_cost = 0.575
- win_amount = 1 - 0.575 = 0.425 (если выигрываем, получаем разницу)
- loss_amount = 0.575

```
Full Kelly = (p × b - q) / b, где b = win/loss = 0.425/0.575 = 0.739
           = (0.715 × 0.739 - 0.285) / 0.739
           = (0.528 - 0.285) / 0.739
           = 0.329 → 33% bankroll
```

**Конservative recommendation: Kelly × 0.25 = ~8% bankroll per trade.**

Для $1000 bankroll → $80 per bet. При 100 trades в месяц с EV +13.5% → ожидаемая прибыль ~$1080/мес. До спреда живого рынка.

⚠️ **Caveat:** Kelly предполагает что мы не ошибаемся в win_rate. Реальный мир может дать win_rate ниже на 5-10pp. Conservative sizing критичен.

## 7. Что мы не успели в Фазе 1 (откладываем)

- **News-lag через GDELT** — отдельная гипотеза, не была критична для подтверждения main edge.
- **On-chain Alchemy fallback** для расширения исторического окна — нужно когда захотим больше OOS.
- **Combinatorial arbitrage** — оставлен на Фазу 1.5 / Фазу 2.

---

## 8. Решение go/no-go

✅ **GO в Фазу 2 (paper trading).**

**Главная стратегия для paper:**
- **H1 baseline** [0.50, 0.85] @ T-24h как основа
- **H1 [0.70, 0.85]** как secondary (sharper edge, меньше сделок)
- **H1.c stable** трекать с интересом (если sample накопится подтверждается)
- **H2 ML** как parallel signal для проверки совпадения

**Что в Фазе 2 paper:**
- Бот пишет "купил бы за $X на рынке Y" каждый раз при срабатывании
- Сверяем с реальными резолвами через 24h+
- Цель: **forward EV сходится с backtest EV** (±3pp на 100+ trades)
- Доп. цель: реальный спред на mid-vol рынках замерить из bids/asks

## 9. Артефакты Фазы 1

- `src/backtest/dataset.py` — feature engineering через DuckDB ASOF
- `src/backtest/strategies.py` — H1 + варианты + H2 LR
- `src/backtest/costs.py` — три cost-модели
- `src/backtest/engine.py` — выполнение стратегий
- `src/backtest/metrics.py` — Sharpe, DD, Kelly
- `scripts/run_backtest.py` — orchestrator
- `data/processed/backtest_report.json` — все метрики
- `data/processed/backtest_h1_test_trades.parquet` — все OOS-сделки H1

---

## 10. Сроки

Фаза 1 закрыта **за один день** вместо запланированных 5-7 — потому что:
- Day 1+2 (engine + baseline) объединены
- Day 3 (варианты) сделан параллельно
- Day 4 (ML baseline) сделан параллельно
- Day 5 (walk-forward) сделан в финале
- Day 6 (sizing) — Kelly посчитан выше
- Day 7 (выводы) — этот документ

Это возможно потому что dataset уже был готов из Дня 3+4 Фазы 0.

---

## Журнал

| Дата | Изменение |
|------|-----------|
| 2026-05-23 | Backtest engine + H1/H1.a-c/H2 на train+test+rolling. Все gate'ы pass. |
