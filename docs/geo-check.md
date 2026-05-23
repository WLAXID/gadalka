# 🌍 Polymarket API — гео-проверка из РФ

**Дата проверки:** 2026-05-23
**Локация:** Россия, без VPN/прокси
**Метод:** прямой HTTP-запрос с локальной машины

---

## ✅ Результат: ВСЕ 3 API доступны без VPN

| API | Endpoint | Статус | Заметка |
|-----|----------|--------|---------|
| Gamma | `https://gamma-api.polymarket.com/markets?limit=1&closed=true` | **200 OK** | 4567 байт, корректный JSON |
| CLOB | `https://clob.polymarket.com/markets?limit=1` | **200 OK** | 1.8 МБ, корректный JSON |
| Data | `https://data-api.polymarket.com/trades?limit=1` | **200 OK** | 749 байт, корректный JSON |

Полная exploration во всех 12 endpoint'ах прошла без 451/403/429.

## Что это значит для gadalka

- ✅ **Фазы 0, 1, 2 (research, backtest, paper)** — работают полностью с прямого подключения.
- ⚠️ **Фаза 3+ (trading с реальных денег)** — read API доступен, но **сам акт торговли** = транзакция в Polygon CTF Exchange с подписанной заявкой → upgrades в логику + KYC blockchain analysis. См. [Reference: Polymarket geo & wallet](../C:\Users\Admin\.claude\projects\X---projects-gadalka\memory\reference-polymarket-geo.md).

## Подводные камни, на которые стоит обратить внимание

1. **Cloudflare может в будущем включить геоблок** — мониторить 429/451 в логах.
2. **WebSocket (для Фазы 2+)** ещё не проверен — нужно отдельно. CLOB WS обычно работает там же где REST.
3. **Site UI** (`polymarket.com`) — это другая поверхность, может блокироваться по гео отдельно. Нас интересует только API.
4. **VPN на проде** — на будущее: иметь готовый VPS в нейтральной юрисдикции (UAE / Грузия) на случай если read API закроют.

## Системные настройки

⚠️ В Python `httpx` берёт `socks4://127.0.0.1:10808` из Windows реестра (видимо, какой-то предыдущий клиент сохранил). socks4 в httpx не поддерживается → нужно `trust_env=False` в клиенте.

В `PolymarketClient` это уже зафиксировано как дефолт.

## Проверочный скрипт

```python
import httpx
with httpx.Client(timeout=15, trust_env=False) as c:
    for name, url in [
        ("gamma", "https://gamma-api.polymarket.com/markets?limit=1"),
        ("clob",  "https://clob.polymarket.com/markets?limit=1"),
        ("data",  "https://data-api.polymarket.com/trades?limit=1"),
    ]:
        r = c.get(url)
        print(f"{name}: {r.status_code} ({len(r.text)} bytes)")
```

Перепроверить можно из любой точки в любое время — занимает 2 секунды.
