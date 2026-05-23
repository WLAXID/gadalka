# 🔮 gadalka

> Research-движок и торговый бот для **Polymarket** (on-chain prediction markets, Polygon).
> Цель: найти и эксплуатировать систематический edge на резолвящихся рынках.

---

## 📚 Оглавление

- [Зачем это](#зачем-это)
- [Философия проекта](#философия-проекта)
- [Текущая фаза](#текущая-фаза)
- [Структура репо](#структура-репо)
- [Стек](#стек)
- [Quick start](#quick-start)
- [Дорожная карта](#дорожная-карта)
- [Полезные ссылки](#полезные-ссылки)

---

## Зачем это

Polymarket — рынок предсказаний на ~$9B TVL с публичными резолвящимися контрактами на Polygon.
~7.6% кошельков прибыльны — edge доказан, но узкий.
Контракты спотовые, в USDC, без перпов и плеча — а значит, **бэктест честный и до денег**.

Этот репо — про то, чтобы:

1. Скачать всю историю резолвнутых рынков,
2. Построить модель оценки вероятностей,
3. Сравнить её предсказания с рыночными ценами,
4. Ставить только там, где **EV положительный** после комиссий и спреда,
5. Не подходить к живым $$ до того, как edge подтверждён на OOS.

## Философия проекта

Уроки `babki` встроены в фундамент:

- 💰 **Спот only, без плеча.** Polymarket этому соответствует by design.
- 🧪 **Сначала бэктест на исторических данных — потом капитал.**
  Babki залил $100 раньше, чем мы доказали edge. Здесь так не будет.
- 🚪 **Жёсткие go/no-go ворота.** Каждая фаза имеет критерии перехода. Не выполнены — STOP.
- 📈 **EV net of fees, slippage, latency.** Никаких "красивых картинок" без учёта реальных издержек.
- 🤖 **Бот-логика всегда explainable.** Сигнал → причина → принятие/отказ.
- 🇷🇺 **Документы и комментарии — на русском.**

## Текущая фаза

**Фаза 0 — Discovery** (3–5 дней, **$0 капитал**).
Задача: понять, какие данные есть бесплатно, можно ли построить честный бэктест,
и стоит ли инвестировать время в Фазу 1.

📄 Детальный план: [`plans/phase-0-discovery.md`](plans/phase-0-discovery.md)

## Структура репо

```
gadalka/
├── plans/              # 📋 Документы фаз — здесь рождается стратегия
│   └── phase-0-discovery.md
├── src/                # Исходники
│   └── collectors/     # Сборщики данных с API
├── notebooks/          # Jupyter — EDA, прототипы, гипотезы
├── data/               # Локальные данные (не в git)
│   ├── raw/            # Сырые ответы API
│   ├── processed/      # Очищенные parquet
│   └── cache/          # Кэш HTTP-запросов
├── docs/               # Технические заметки (схемы API, эндпоинты)
├── README.md
├── .gitignore
└── requirements.txt
```

## Стек

- **Python 3.11+**
- `httpx` — HTTP-клиент с retry/backoff
- `duckdb` + `pyarrow` — хранение и аналитика parquet
- `pandas` / `polars` — DataFrame-операции
- `jupyter` — EDA
- `scikit-learn` — baseline-модели
- (позже) `web3.py` — для on-chain reads из Polygon

## Quick start

```powershell
# Создать venv
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Установить зависимости
pip install -r requirements.txt

# Запустить Jupyter
jupyter lab
```

## Дорожная карта

| Фаза | Срок | Капитал | Цель | Gate перехода |
|------|------|---------|------|---------------|
| **0. Discovery** | 4–6 дней | $0 | Landscape APIs + existing projects, разведка Polymarket API, feasibility бэктеста. | Доступна история цен до резолва на ≥500 рынках |
| **1. Backtest** | 5–7 дней | $0 | Построить baseline-модель + 2–3 стратегии. OOS-валидация. | EV > 0 net of fees на OOS |
| **2. Paper trade** | 2–4 нед | $0 | Бот пишет "купил бы за $X". Сверяем с реальностью. | Forward-EV сходится с backtest |
| **3. Pilot** | 4–6 нед | $100 | Реальные ставки минимального размера. | Net P&L > 0 после ≥100 ставок |
| **4. Scale** | — | $500 → $2–5k | Kelly fractional sizing, диверсификация по стратегиям. | Hard DD stop −30% |

## Полезные ссылки

- [Polymarket Docs](https://docs.polymarket.com)
- [Rate Limits](https://docs.polymarket.com/api-reference/rate-limits)
- [CLOB API](https://docs.polymarket.com/developers/CLOB/introduction)
- [Gamma API (events/markets metadata)](https://docs.polymarket.com)

---

🔮 _"Predicting is hard. Especially the future." — Niels Bohr (or so they say)_
