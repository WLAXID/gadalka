# 📝 Фаза 2 — Paper Trading

**Срок:** 2–4 недели (forward time)
**Капитал:** $0
**Статус:** 🟡 На верификации пользователя

---

## 🎯 Цели

1. Запустить **live signal generation** на текущих активных рынках (не historic).
2. Каждое срабатывание H1 — записать `(timestamp, condition_id, entry_price, predicted_outcome)`.
3. Через ≥24h после резолва каждого рынка — сравнить факт с прогнозом, посчитать P&L.
4. Замерить **forward EV** и сравнить с backtest EV (+13.5%). Цель — сходимость на ±3pp.
5. Замерить **реальный спред и slippage** на mid-vol рынках.
6. Накопить ≥100 trades для статзначимости.

## 🚫 Что НЕ делаем

- ❌ Не торгуем реальными деньгами
- ❌ Не подключаем кошелёк
- ❌ Не делаем UI или сложный мониторинг
- ❌ Не модифицируем стратегию по ходу — фиксируем H1 baseline на старте

## 📐 Дизайн системы

### Архитектура

```
┌─────────────────────────────────────────────────────────┐
│  scripts/paper_trader.py                                │
│                                                          │
│   1. ETL loop (каждые 15 мин):                          │
│      - PolymarketClient.gamma_markets(active=True)      │
│      - Для каждого активного: запрос clob_prices_history│
│      - Считаем price_yes_t24h, price_yes_t7d            │
│      - Применяем H1.select() → list of trades           │
│                                                          │
│   2. State store (data/paper/state.db):                 │
│      - taken_trades (idempotency: 1 trade per market)   │
│      - pending_trades (waiting for resolve)             │
│      - resolved_trades (final P&L)                      │
│                                                          │
│   3. Resolve loop (каждые 60 мин):                      │
│      - For each pending — проверка closed/resolved      │
│      - Если резолв известен → пишем P&L                 │
│                                                          │
│   4. Daily report (cron 23:59 UTC):                     │
│      - Свежий P&L, win rate, EV vs backtest             │
│      - data/paper/daily_<date>.json                     │
└─────────────────────────────────────────────────────────┘
```

### Правила входа (H1 baseline зафиксирован)

- `0.50 ≤ price_yes_t24h < 0.85` (т.е. за 24h до резолва YES в этой полосе)
- Сделка засчитывается **один раз** на market (даже если signal появляется снова)
- Реальное entry_price = `mid_price * (1 + 0.015/2)` (учёт спреда)
- Stake = $1 (paper) — равные ставки
- Hold to resolve

### Правила выхода

- Hold to resolution. Не выходим раньше.
- Если рынок отменён / резолв unclear → exclude trade (не считаем убытком)

### Edge cases

- **Market без полной T-24h истории** (новые рынки или короткоживущие) → skip
- **Market с volume < $100** → skip (signal на пыли неблагонадёжен)
- **Market closed между двумя ETL-проходами** → пишем trade с финальным price из последнего snapshot

## 📅 План

### Week 1 — Setup
- [ ] `src/paper/` модуль с лупами и state store
- [ ] SQLite (через DuckDB) для state
- [ ] Cron / Docker compose для запуска ETL/resolve loops
- [ ] Smoke test 24h на reality

### Week 2 — Live data accumulation
- [ ] 15-минутный ETL генерит trades
- [ ] Resolve loop пишет P&L
- [ ] Daily report
- [ ] Replicating ≥30 trades

### Week 3 — Анализ
- [ ] Forward EV vs backtest EV
- [ ] Реальный спред (median ask-bid из bids/asks)
- [ ] Реальный slippage (price_at_signal vs price_at_resolution)
- [ ] Distribution of trade outcomes

### Week 4 — Финал
- [ ] ≥100 trades накоплено
- [ ] `plans/phase-2-findings.md`
- [ ] Go/no-go в Фазу 3 (pilot $100)

## 🚨 Critical gates

| Gate | Условие | Действие если не выполнено |
|------|---------|----------------------------|
| Week 2 | ≥30 trades, не падает | Дебаг loop'ов |
| Week 3 | Forward EV > +5% (с реальным спредом) | Investigate gap vs backtest |
| Week 4 | ≥100 trades, forward EV в ±5pp от backtest | STOP, обсудить почему расходится |
| Week 4 | Max DD < 25% (за весь период) | Conservative sizing в Фазе 3 |

## 🚧 Риски

| Риск | Митигация |
|------|-----------|
| Реальный спред >> 1.5% → EV упадёт | Замеряем реальные bids/asks; если >3% — пересматриваем filter |
| Time delay между ETL проходами скажется | Замеряем gap: ETL_ts vs actual_close_ts |
| Polymarket изменит API | Кэшируем raw responses, можем пере-парсить |
| Edge провалится на forward | Это и есть тест — если так, не идём в Фазу 3 |
| RU geo заблокирует read API на ходу | Имеем VPN-кошелёк готовый |

## 🔧 Технические задачи

### `src/paper/scheduler.py`
- Async scheduler с двумя tasks: ETL (15 мин), resolve (60 мин)
- Грaceful shutdown

### `src/paper/state.py`
- DuckDB-based persistent state
- Таблицы: `paper_trades` (`condition_id PK, entry_ts, entry_price, buy_cost, stake`)
- Таблица `paper_resolutions` (`condition_id PK, resolve_ts, payout, pnl`)
- Idempotency через UPSERT

### `src/paper/signal.py`
- Tonkий wrapper над `BuyFavoriteStrategy` который работает на текущих рынках
- Считает `price_yes_t24h` по prices-history с интервалом=1h на последние 25h

### `src/paper/report.py`
- Ежедневный отчёт в JSON + markdown
- Summary statistics

## 📋 Что нужно от пользователя

1. **Подтвердить стратегию для paper:** только H1 baseline или + H1 [0.70, 0.85] / H1.c stable?
2. **Подтвердить размер ставки:** $1 на trade (для подсчёта метрик)?
3. **Подтвердить деплой:** локально (Windows machine) или на сервере (Linux VPS)?
4. **Подтвердить продолжительность:** 4 недели стандарт?
5. **OFAC blocker:** напоминание — в paper мы не торгуем, но в Фазе 3 будет проблема. Решение позже.

---

## Журнал

| Дата | Изменение |
|------|-----------|
| 2026-05-23 | Первая версия после Phase 1 findings. H1 baseline зафиксирован. |
