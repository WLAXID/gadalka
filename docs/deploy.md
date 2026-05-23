# 🚀 Deploy — Gadalka Paper Trader

> 24/7 paper trader + Telegram-панель в Docker.

---

## Что внутри

Один сервис `gadalka-paper`:
- **Scheduler:** 3 async-loops
  - ETL (раз в 15 мин) — скан рынков, поиск сигналов H1
  - Resolve (раз в 1 час) — проверка резолвов pending-ставок
  - Daily report (23:59 UTC) — ежедневный отчёт в Telegram
- **Telegram bot:** long-polling, доступ только владельцу

Состояние в **DuckDB** на именованном томе → переживает рестарт/обновление.

## Быстрый запуск

### 1. Конфиг

```bash
cp .env.example .env
nano .env  # заполнить TG_BOT_TOKEN и TG_OWNER_ID
```

Бот: получить токен у `@BotFather`.
Owner ID: написать `@userinfobot` любое сообщение, ответит id.

### 2. Запуск

```bash
docker compose up -d --build
docker compose logs -f          # смотреть логи
```

Бот напишет в Telegram «🚀 Gadalka стартовала».

### 3. Проверка

В Telegram отправить `/start` боту → должно прийти главное меню с кнопками.

### Управление

```bash
docker compose restart                  # перезапустить
docker compose down                     # остановить (данные сохранены)
docker compose down -v                  # остановить + удалить тома (⚠ потеря данных)
docker compose logs -f paper            # логи в реальном времени
docker compose exec paper bash          # внутрь контейнера
```

## Серверная развёртка

### Минимальные требования
- Linux x86_64 (Ubuntu 22.04+ / Debian 12)
- Docker 24+ и Docker Compose v2
- ~256 MB RAM (DuckDB лёгкий)
- 1 GB диска (для логов и DB)

### Шаги

```bash
# 1. Клонировать репо
git clone https://github.com/WLAXID/gadalka.git /opt/gadalka
cd /opt/gadalka

# 2. Конфиг
cp .env.example .env
nano .env  # заполнить токены

# 3. Запуск
docker compose up -d --build

# 4. Авторестарт при ребуте сервера
# (restart: unless-stopped в docker-compose.yml уже настроен)

# 5. Логи
docker compose logs -f --tail 100
```

### Обновления

```bash
cd /opt/gadalka
git pull
docker compose up -d --build           # пересобирает образ, рестартует
```

### Бэкап БД

```bash
# Скопировать paper.duckdb с тома
docker compose cp paper:/app/data/paper.duckdb ./backups/paper-$(date +%F).duckdb
```

## Telegram-команды и кнопки

| Команда | Кнопка | Что делает |
|---------|--------|------------|
| `/start` | — | Главное меню |
| `/stats` | 📊 Сводка | Текущие метрики: открытых, резолвнутых, win rate, P&L |
| `/pending` | 💼 Открытые | Список открытых ставок |
| `/recent` | 📜 Последние | Последние 10 резолвов с P&L |
| `/health` | ❤️ Здоровье | Статус ETL/Resolve loops, последние ошибки |
| `/pause` | ⏸ Пауза | Остановить генерацию новых ставок |
| `/resume` | ▶ Запустить | Возобновить |
| `/settings` | ⚙ Настройки | Параметры стратегии |
| `/help` | ❓ Помощь | Краткая справка |

### Push-уведомления

Автоматически приходят:
- 📍 **Новая ставка** — когда сигнал срабатывает
- ✅/❌ **Резолв** — после закрытия рынка (если |P&L| > $0.10)
- 📅 **Ежедневный отчёт** — в 23:59 UTC
- ⚠ Ошибки (если случаются) — фиксируются в `/health`

## Структура данных

```
/app/data/paper.duckdb       # state (на томе gadalka-data)
  ├─ paper_trades            # все ставки (pending/resolved/cancelled)
  ├─ paper_resolutions       # P&L резолвнутых
  ├─ paper_events            # журнал (info/warning/error)
  └─ paper_settings          # KV (paused, last_etl_ts, ...)

/app/logs                    # на томе gadalka-logs (сейчас все в stdout)
```

## Безопасность

- ✅ `.env` не в репо (gitignored)
- ✅ Контейнер запускается под непривилегированным `app` (uid 10001)
- ✅ Auth-middleware дропает любые сообщения от не-владельца
- ✅ DuckDB на томе, не в образе

## Траблшутинг

**Бот не отвечает:**
```bash
docker compose logs paper | grep -i tg
# Проверь что нет ошибок аутентификации
# Проверь что TG_OWNER_ID = твой реальный user_id
```

**Сигналы не находятся:**
```bash
docker compose logs paper | grep -i signal
# Проверь сколько кандидатов и сколько сигналов прошло
# Возможно текущая ситуация на рынке вне диапазона [0.50, 0.85]
```

**Гео-блок Polymarket:**
```bash
# Если на сервере другая локация и API заблокирован — проверь:
docker compose exec paper python -c "
import httpx
r = httpx.get('https://gamma-api.polymarket.com/markets?limit=1', timeout=10)
print(r.status_code, r.text[:200])
"
```

**Перенастроить стратегию без пересборки:**
- Изменить `PAPER_STRATEGY_LOW` / `PAPER_STRATEGY_HIGH` в `.env`
- `docker compose restart paper`

## Стратегия (H1 baseline)

- **Сигнал:** цена YES за 24 часа до резолва в [0.50, 0.85]
- **Размер ставки:** $1 (paper)
- **Costs:** 2% fee + 1.5% spread + 0.5% slippage (≈ 4% drag)
- **Удержание:** до резолва (без exit раньше)

См. [`plans/phase-1-findings.md`](../plans/phase-1-findings.md) для деталей бэктеста.

## Что НЕ делает paper trader

- ❌ НЕ торгует реальными деньгами
- ❌ НЕ пишет в Polymarket API
- ❌ НЕ держит кошелёк
- ❌ НЕ принимает никаких внешних запросов кроме Telegram

Это **наблюдатель-симулятор** на 4 недели чтобы подтвердить forward-edge.
