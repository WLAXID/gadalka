# 📝 Фаза 2 — Расширение «уехал на месяц, вернулся с датасетом»

**Срок:** 1–2 дня кода
**Статус:** 🟡 На верификации
**База:** [phase-2-paper.md](phase-2-paper.md)

---

## 🎯 Цель

Дополнить paper-trader так, чтобы он:

1. Собирал **богатый датасет** (а не только entry/resolve) — траектории цен, snapshots orderbook, near-miss кандидаты.
2. Был **устойчив без присмотра** (heartbeat, backup, log rotation).
3. Гарантировал **чистоту выборки** (фильтры по endDate и event_id, stuck-pending warnings).

После этого можно с чистой совестью оставлять на 30 дней и получить ~60–100 trades + полноценный dataset для пост-анализа.

---

## 📦 Что добавляем

### 1. Trajectory tracking

**Новый loop** `_trace_loop` (interval 1h, конфиг `TRACE_INTERVAL_S=3600`):

- Берёт все pending trades.
- Для каждого `clob_midpoint(token_id)`.
- Пишет в новую таблицу `paper_price_trace (trade_id, ts, mid, bid, ask)`.

**Новая таблица:**

```sql
CREATE TABLE paper_price_trace (
    id BIGINT PRIMARY KEY,          -- nextval(paper_trace_seq)
    trade_id BIGINT NOT NULL,
    ts BIGINT NOT NULL,
    mid DOUBLE,
    bid DOUBLE,
    ask DOUBLE,
    spread_pct DOUBLE               -- (ask-bid)/mid, NULL если книга пуста
);
CREATE INDEX paper_trace_trade_idx ON paper_price_trace (trade_id, ts);
```

**Объём:** 60 trades × 24h × 30d ≈ 43k строк/мес. Копейки.

### 2. Market snapshots at entry

В `signal.py` при формировании сигнала **до** записи trade'а делаем `clob_book(token_id)` и сохраняем верхний уровень + 5 уровней depth в новую таблицу.

```sql
CREATE TABLE paper_trade_snapshots (
    trade_id BIGINT PRIMARY KEY,
    ts BIGINT NOT NULL,
    bid DOUBLE,
    ask DOUBLE,
    mid DOUBLE,
    spread_pct DOUBLE,
    bid_depth_5 DOUBLE,             -- сумма size топ-5 bids
    ask_depth_5 DOUBLE,
    raw_book TEXT                   -- полный JSON книги для архива
);
```

Это потом даёт ответ на ключевой вопрос: «насколько realistic был cost-model в backtest».

### 3. Scan-snapshots в БД (не в settings)

Сейчас `last_scan_stats` перетирается каждый скан → near-miss кандидаты теряются. Сохраняем в БД.

```sql
CREATE TABLE paper_scan_dump (
    id BIGINT PRIMARY KEY,
    scan_ts BIGINT NOT NULL,
    bucket TEXT NOT NULL,            -- 'in_range' | 'near_below' | 'near_above' | 'candidate'
    condition_id TEXT,
    token_id TEXT,
    slug TEXT,
    price_yes DOUBLE,
    volume DOUBLE,
    end_date_iso TEXT
);
CREATE INDEX paper_scan_dump_ts_idx ON paper_scan_dump (scan_ts);
```

**Retention:** скрипт чистки `older_than(45d)` (вызывается из daily report loop). За 30d при 96 сканах/день × ~20 candidates ≈ 58k строк.

### 4. Heartbeat alert

В scheduler добавить `_heartbeat_loop` (каждые 10 мин):

- Если `last_etl_ts` старше `HEARTBEAT_THRESHOLD_S` (default 7200 = 2h) → push в TG.
- **Throttle:** не чаще раза в 6h, чтобы не спамить.
- Состояние throttle хранится в `paper_settings` как `last_heartbeat_alert_ts`.

### 5. Auto-backup БД

Daily-loop в 03:00 UTC (отдельно от daily report):

- `state.make_dump('data/backups/paper_YYYYMMDD.duckdb')`
- Rotation: удалить дампы старше `BACKUP_RETENTION_DAYS` (default 14).

### 6. Log rotation в docker-compose

```yaml
services:
  paper:
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "5"
```

### 7. Корректность выборки (фильтры в signal.py)

- **Skip endDate > now + MAX_TTL_DAYS** (default 30). Конфиг `PAPER_MAX_MARKET_TTL_DAYS=30`.
- **Max 1 trade per event_id**: добавить в `paper_trades` колонку `event_id`, проверка `has_trade_for_event`. Если у рынка нет `eventId` — фильтр не применяется.
- **Stuck-pending warning**: в resolver, если `now - end_date > 7d` и trade всё ещё pending → log_event WARNING (не делаем больше, просто видимость).

### 8. Health-метрика в БД

В `summary_stats()` добавить:

- `last_scan_age_s`
- `last_resolve_age_s`
- `error_count_24h`
- `trace_points_24h`
- `backup_age_s`

Отображаем в `/health` и daily report.

---

## 🚫 Что НЕ делаем

- ❌ Не меняем стратегию H1 (правка номиналов range = смена эксперимента).
- ❌ Не добавляем UI/dashboard сверх TG-бота.
- ❌ Не интегрируем S3/external backup — backup только локально (за пределы Docker volume).
- ❌ Не пишем тесты на каждый loop — ограничимся smoke-проверками.
- ❌ Не трогаем resolver-логику (cancelled/split уже пофикшено в 55d1516).

---

## 📋 Конфиг (новые env-vars в PaperConfig)

```python
trace_interval_s: int = 3600            # PAPER_TRACE_INTERVAL_S
heartbeat_threshold_s: int = 7200       # PAPER_HEARTBEAT_THRESHOLD_S
heartbeat_throttle_s: int = 21600       # PAPER_HEARTBEAT_THROTTLE_S (6h)
max_market_ttl_days: int = 30           # PAPER_MAX_MARKET_TTL_DAYS
backup_retention_days: int = 14         # PAPER_BACKUP_RETENTION_DAYS
backup_time: str = "03:00"              # PAPER_BACKUP_TIME (UTC)
backup_dir: str = "data/backups"        # PAPER_BACKUP_DIR
```

---

## ✅ Smoke-критерии

После реализации проверяем локально (без Docker):

1. `python -m src.paper` стартует, в логах видны 5 loops (etl/resolve/trace/heartbeat+backup/daily).
2. В TG приходит `🚀 Gadalka стартовала` с упоминанием новых интервалов.
3. После форсированного `state.set_setting("last_etl_ts", str(int(time.time()) - 8000))` heartbeat-loop шлёт алерт в течение 10 мин.
4. `state.make_dump` пишет в `data/backups/` и старые файлы чистятся.
5. `paper_price_trace`, `paper_trade_snapshots`, `paper_scan_dump` создаются при init_schema, INSERT'ы работают.
6. В `/health` видно `error_count_24h`, `trace_points_24h`, `backup_age_s`.

---

## 🔢 Порядок реализации (PR'ы)

1. **schema + state.py**: новые таблицы, методы insert/query, миграция (CREATE IF NOT EXISTS).
2. **signal.py**: фильтр TTL+event_id, snapshot при entry, scan_dump запись.
3. **scheduler.py**: `_trace_loop`, `_heartbeat_loop`, `_backup_loop`.
4. **bot.py**: обновить `/health` и daily report новыми метриками.
5. **docker-compose**: log rotation, volume для backups.

Можно одним commit'ом — всё связано.

---

## ⏱ Бюджет

~600–800 строк кода + тесты. 4–6 часов работы. После merge — можно перезапускать paper-trader и спокойно уезжать.
