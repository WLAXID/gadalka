"""Smoke-test для KeyPool: загружает api_keys.json и печатает статистику.

Запуск из корня проекта::

    python scripts/check_keypool.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Windows-консоль по умолчанию cp1251 — переключаем stdout/stderr в UTF-8,
# чтобы emoji и кириллица печатались корректно.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# Чтобы импорты src.* работали при запуске из корня
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.api.keypool import KeyPool  # noqa: E402
from src.api.providers import PROVIDERS, core_providers  # noqa: E402


async def main() -> None:
    keys_file = ROOT / "api_keys.json"
    if not keys_file.exists():
        print(f"❌ Файл с ключами не найден: {keys_file}")
        sys.exit(1)

    pool = KeyPool.from_file(keys_file)
    print(f"\n📦 Загружено провайдеров: {len(pool.providers())}")
    print(f"\n🎯 Core-провайдеры для gadalka:")
    for name in core_providers():
        if pool.has(name):
            print(f"  ✅ {name:<14} — {pool.count(name)} ключей")
        else:
            print(f"  ❌ {name:<14} — НЕТ в пуле")

    print(f"\n📊 Полная статистика:\n")
    print(pool.stats_pretty())

    # Простой round-robin тест: 5 раз acquire/release Polygon.io
    print(f"\n🔄 Тест ротации (5 запросов polygon):")
    if pool.has("polygon"):
        for i in range(5):
            async with pool.use("polygon") as key:
                # Маскируем ключ — показываем только хвост
                tail = key[-8:] if len(key) > 8 else key
                print(f"  {i+1}. ключ ...{tail}")

    print(f"\n💾 Статистика после теста:\n")
    print(pool.stats_pretty())


if __name__ == "__main__":
    asyncio.run(main())
