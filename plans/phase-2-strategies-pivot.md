# 🔄 Phase 2 — Pivot стратегии

**Дата:** 2026-05-24
**Статус:** Подготовлено, ждёт рестарта на сервере

---

## Что произошло

После repr backtest на 54k markets (sharded pull без selection bias):
- **H1 [0.50, 0.85] BUY YES — НЕ работает** (EV -2.15% на 5760 trades)
- Старый +20% EV был **artifact 100% selection bias**
- Подтверждено Manifold cross-validation (-19.5% EV)

Прогон grid search 72 стратегий + stress test показал **2 выживших**:
- **CRYPTO + drift>+10pp BUY YES** (realistic +7.7%, P>0 = 99.6%)
- **CRYPTO underdog [0.15, 0.50] BUY YES** (realistic +5.5%, P>0 = 98.1%)

## Что меняем в live

Простейший pivot — **одна** стратегия `crypto_underdog` без category-momentum (она требует доп. API запросы для drift, отложим).

### Параметры

| Было | Стало |
|---|---|
| `PAPER_STRATEGY_LOW=0.50` | **0.15** |
| `PAPER_STRATEGY_HIGH=0.85` | **0.50** |
| (нет фильтра) | `PAPER_CATEGORY_FILTER=crypto` |

### Что делает

1. Сканер каждые 15 мин пагинирует все active markets
2. Для каждого:
   - volume >= $10k
   - endDate в [сейчас, +7 дней]
   - **категория = crypto** (по keywords в question)
   - YES price в [0.15, 0.50] — недооценённый underdog
3. Открывает $1 ставку YES
4. Ждём резолва, считаем PnL

### Ожидания

По stress test на репрезентативной выборке:
- Realistic median EV/$ = **+5.5%**
- Realistic p05 = +1.1%
- P(EV>0) = **98.1%**

За месяц paper ожидаем ~30 trades (crypto markets реже H1 на ~3x), edge даст ~$1.50 PnL на $30 invested.

## Что не делаем (откладываем)

- **Momentum (drift>+10pp)** — требует доп. API запросы для T-7d prices. Это +10000 запросов на скан, можем зацепить rate-limit. Лучше после того как простой crypto_underdog подтвердит структурный edge.
- **Sport NO, Longshots** — не выжили в realistic stress test (-0.9% и +4.1%@P92%). Не лезем.
- **Ensemble нескольких стратегий** — требует refactor Signal + state.py UNIQUE constraint. Лучше после валидации одной стратегии.
- **ML модели** — нет смысла пока простые правила не подтвердили direction в live.

## Что в коде уже сделано

- `src/paper/signal.py` — добавлен `_categorize_question(q)` и `skip_wrong_category` counter
- `src/paper/config.py` — добавлен `category_filter: str` (env `PAPER_CATEGORY_FILTER`)
- Категории: `crypto`, `sport`, `politics`, `weather`, `economy`, `other`

Если `PAPER_CATEGORY_FILTER=""` — фильтр выключен (старое поведение).
Если `PAPER_CATEGORY_FILTER=crypto` — открываем только crypto markets.

## Что делать на сервере

1. Подтянуть код:
```bash
cd /opt/gadalka
git pull
```

2. Обновить `.env` (на сервере):
```bash
# Добавить/изменить:
PAPER_STRATEGY_LOW=0.15
PAPER_STRATEGY_HIGH=0.50
PAPER_CATEGORY_FILTER=crypto
```

3. Удалить старые pending H1 ставки (они под другую стратегию):
```bash
docker compose stop
docker compose run --rm --entrypoint python paper -c "import duckdb; c=duckdb.connect('/app/data/paper.duckdb'); c.execute(\"DELETE FROM paper_trade_snapshots WHERE trade_id IN (SELECT trade_id FROM paper_trades WHERE status='pending')\"); c.execute(\"DELETE FROM paper_trades WHERE status='pending'\"); print('cleaned:', c.execute('SELECT COUNT(*) FROM paper_trades').fetchone()[0]); c.execute('CHECKPOINT'); c.close()"
docker compose up -d --build
docker compose logs -f
```

4. **▶ Запустить** в TG-боте чтобы снять паузу.

Через ~30-50 закрытых ставок (3-4 недели) — сравним live EV с backtest +5.5%.

## Что ждать в логах

```
[signal] получено 8000 active рынков
[signal] X signals; below=A above=B ttl_far=C wrong_cat=D event_taken=E taken=F
```

`wrong_cat` должен быть большим (~80-95% всех в полосе) — это норм, мы фильтруем агрессивно.

Если за день ставок открывается **0** — стратегия слишком агрессивна, релакснуть фильтр (расширить категории или price band).
Если **больше 30-50/день** — что-то не так с категорией.

## Honest expectations

Это **не** «золотая жила». Это:
- Простая, проверенная (через stress test) гипотеза на грани значимости
- Возможно работает (+5% EV) — возможно нет (Manifold n=18, слабая статистика)
- Paper покажет правду
