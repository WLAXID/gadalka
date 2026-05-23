# 🔑 KeyPool — ротация API-ключей

> `src/api/keypool.py` — менеджер пула ключей с round-robin ротацией
> и автоматическим cooldown на ошибках.

---

## Что делает

- Загружает все ключи из `api_keys.json`
- Round-robin ротация по списку для каждого провайдера
- Помечает ключ «остывающим» при ошибках:
  - `429` → 60s cooldown
  - `401/403` → 1ч cooldown (возможно ключ битый)
  - `5xx` → 10s cooldown
  - прочие → 5s
- Ведёт статистику по каждому ключу (uses, errors, last_error_code)
- Thread-safe для asyncio через per-provider locks
- Если все ключи в cooldown — ждёт ближайшего

## Структура `api_keys.json`

```json
{
  "providers": {
    "polygon": {
      "keys": [
        {"key": "abc123...", "label": "default"},
        {"key": "def456...", "label": "default"}
      ]
    },
    "alchemy": {
      "keys": [
        {"key": "https://polygon-mainnet.g.alchemy.com/v2/...", "label": "default"}
      ]
    }
  }
}
```

> ⚠️ Файл в `.gitignore`. **Никогда не коммитим.**

## Использование

### Базовое

```python
from src.api.keypool import KeyPool

pool = KeyPool.from_file("api_keys.json")

# Получить и автоматически отпустить ключ
async with pool.use("polygon") as key:
    response = await client.get(
        "https://api.polygon.io/v2/aggs/ticker/X:BTCUSD/range/1/day/...",
        params={"apiKey": key},
    )
    response.raise_for_status()
```

### С автоматическим cooldown на ошибках

Если исключение содержит `status_code` (или `response.status_code`), ключ
автоматически уходит в cooldown:

```python
async with pool.use("polygon") as key:
    response = await client.get(url, params={"apiKey": key})
    response.raise_for_status()  # ← если 429, KeyPool пометит ключ
```

### Метрики

```python
print(pool.stats_pretty())
# provider        keys  avail   uses errors
# --------------------------------------------
# alchemy          115    115     42      0
# polygon           40     38    100      2
# tavily             8      8      5      0
```

## Провайдеры в `src/api/providers.py`

Справочник: что делает каждый провайдер, как использовать ключ
(URL / query param / Bearer header / X-Api-Key), free tier, релевантность
(`core` / `useful` / `backup` / `ignore`).

```python
from src.api.providers import PROVIDERS, core_providers

print(core_providers())
# ['alchemy', 'chainstack', 'polygon', 'newsapi', 'tavily', 'perplexity']
```

## Запуск smoke-теста

```powershell
python scripts/check_keypool.py
```

Печатает:
- сколько провайдеров загружено
- сколько core-провайдеров покрыто
- полную таблицу статистики
- проверку ротации на 5 запросах Polygon.io

## Что НЕ делает (out of scope)

- Не сохраняет состояние uses/errors между сессиями (пока)
- Не балансирует по latency / health-метрикам
- Не работает с rate-limit budget per-провайдер (только cooldown reactive)
- Не подменяет HTTP-клиент — это просто менеджер строк-ключей

Если понадобится — добавим в Day 2/3 Фазы 0.
