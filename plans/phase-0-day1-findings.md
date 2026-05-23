# 📋 Phase 0 — Day 1 Findings

**Дата:** 2026-05-23
**Документы:** [`docs/apis-landscape.md`](../docs/apis-landscape.md), [`docs/existing-projects.md`](../docs/existing-projects.md)

---

## TL;DR

День 1 принёс **5 серьёзных корректировок** стратегии. Главные:

1. **РФ в OFAC-блоке Polymarket** → trading через РФ-IP заблокирован. Gadalka до решения этого вопроса = **research/paper-trading проект**.
2. **Favorite-longshot bias на Polymarket ОПРОВЕРГНУТ** (Reichenbach & Walther 2026, 124M трейдов). Longshot-гипотеза опускается до sanity-check.
3. **Mid-volume рынки ($10k–$100k daily) — главный кандидат edge.** HFT-клан занял топы и low-vol хвост; mid слабее конкурентен.
4. **Используем `py-clob-client-v2`** (официальный, май 2026, MIT) для CLOB write-ops. Gamma/Data — свой `httpx`-клиент.
5. **Dune Analytics** имеет десятки готовых Polymarket dashboards → экономит недели на самосборе. Используем в Day 4 для price-history.

---

## 1. Critical блокер: OFAC + РФ

**Что есть:**
- Россия в санкционном перечне Polymarket
- VPN детектится (device fingerprinting + IP + KYC blockchain analysis)
- Freeze withdrawals задокументированы для заблокированных юрисдикций

**Что это меняет:**
- ✅ Фазы 0, 1, 2 (research, backtest, paper trading) — без изменений, не требуют trading-доступа
- ⚠️ Фаза 3 ($100 pilot) — под вопросом. Варианты:
  - Pivot на Kalshi или Manifold Markets
  - VPS в нейтральной юрисдикции + локальная KYC
  - Остаёмся paper-only forever (gadalka = research-проект)

**Решение на сейчас:** не тратим время на wallet setup. В Day 2 — приоритет проверки read API из РФ.

См. [Reference: Polymarket geo & wallet](../C:%5CUsers%5CAdmin%5C.claude%5Cprojects%5CX---projects-gadalka%5Cmemory%5Creference-polymarket-geo.md).

---

## 2. Favorite-longshot bias не работает на Polymarket

**Источник:** Reichenbach & Walther 2026, анализ 124M трейдов.

**Главный вывод:** на Polymarket прибыльные трейдеры играют **favorites чаще**, не longshots. Это **инверсия** классического FLB из спортивных букмекеров.

**Что меняем в гипотезах для Фазы 1:**
- Гипотеза "longshot с $10" — sanity check, не главная ставка
- Главный кандидат теперь: **mid-volume mispricing**
- В бэклог: combinatorial arbitrage (arXiv 2508.03474), patient market-making, news-lag в нишах

---

## 3. Картa вытоптанности edges

| Edge | Статус | Кто там сейчас |
|------|--------|-----------------|
| HFT-арб BTC/ETH 5–15 мин | 🔴 Вытоптан | HFT-клан, sub-100ms cutoff, avg time-to-fill 2.7 сек |
| Cross-platform arb топовых рынков (PM↔Kalshi) | 🔴 Вытоптан | Топ-боты закрывают за секунды/минуты |
| Twitter sentiment → fade retail | 🔴 Деградировал | Шум>сигнал с 2024 |
| Naive longshot bias | 🟡 Опровергнут | Не работает как baseline |
| **Mid-volume mispricing ($10k–$100k daily)** | 🟢 **Свободнее** | Конкуренция средняя, не latency-зависим |
| News-lag в нишевых категориях | 🟢 Свободнее | Нужны собственные news feeds |
| Combinatorial arb | 🟢 Свободнее | Требует тонкой математики |
| Patient market-making mid-vol | 🟢 Свободнее | Альтернатива HFT-MM |

---

## 4. Стек: что используем

### Polymarket data access
- **CLOB write-ops:** `py-clob-client-v2` (официальный, MIT, активный)
- **Gamma + Data API:** свой `httpx`-клиент (нет официальных Python SDK)
- **Альтернатива:** `quantpylib.wrappers.polymarket` (унифицированный async, рассмотреть в Day 2)
- **WebSocket:** свой клиент, референс — TS-репо Polymarket + `pascal-labs/polymarket-sdk`

### Historical data (КРИТИЧНО для Фазы 1)
1. **Dune Analytics** — 1й приоритет. Готовые dashboards с 4 годами истории, бесплатные queries.
2. **Goldsky** — официальный partner Polymarket, production-grade. Бэклог для Фазы 2+.
3. **On-chain Polygon RPC** — fallback, требует не-public RPC (public throttle 40 req/s, sign-in required).
4. **Marketlens / PolyBackTest** — закупаемые datasets, если глубина критична.

### Backtest engine (Фаза 1)
- **NautilusTrader** + extension `evan-kolberg/prediction-market-backtesting`

### News & social (для Фазы 1+ news-lag стратегии)
- **GDELT** (бесплатно, важнее чем казалось — Twitter free tier убран в феврале 2026)
- NewsAPI, Tavily, Perplexity API — рассмотреть в зависимости от категории
- Twitter/X — стал дорогим ($0.005/read pay-per-use), legacy Pro $5000

### LLM (для news → P(yes) features)
- **Gemini 3 Flash** ($0.50/$3 per 1M, 1M context) — массовая обработка
- **DeepSeek V3.2** ($0.27 input) — самое дешёвое
- Claude/OpenAI — для quality-critical случаев

---

## 5. Гипотезы для Фазы 1 (обновлённый список)

В порядке приоритета:

1. **Mid-volume mispricing** — ML-модель P(yes) на рынках $10k–$100k daily volume
2. **Combinatorial arbitrage** — поиск неконсистентностей между связанными рынками
3. **News-lag в нишах** — science, regional politics, не-топ-категории
4. **Patient market-making mid-vol** — широкий спред на рынках со средней ликвидностью
5. **(downgraded)** Longshot bias — sanity-check на нашем датасете, не приоритет

---

## 6. Решения, которые НЕ принимаем сегодня

Это в Day 5/6 после полного EDA:
- Финальный выбор top-3 гипотез для бэктеста
- Pivot vs continue решение по поводу OFAC
- Размер MVP-датасета для Фазы 1

---

## ✅ Чек-лист Day 1

- [x] APIs landscape собран (`docs/apis-landscape.md`, 297 строк)
- [x] Existing projects обзор (`docs/existing-projects.md`, 444 строки)
- [x] Известные edges и их вытоптанность задокументированы
- [x] Решение по SDK: `py-clob-client-v2` + свой клиент для Gamma/Data
- [x] Новые гипотезы для Фазы 1 сформулированы
- [x] Critical OFAC-блокер выявлен и зафиксирован

## Что в Day 2

- Поднять Python 3.11 venv, поставить requirements
- Базовый `httpx`-клиент для Gamma/Data, попробовать `py-clob-client-v2` для CLOB
- Sample-запросы ко всем endpoint'ам, документация полей в `docs/api-schemas.md`
- **Приоритет:** проверить geo-доступность из РФ без VPN
- Альтернатива: попробовать `quantpylib.wrappers.polymarket` как единый wrapper

---

## 🔄 Изменения по плану Фазы 0

Изменений в `plans/phase-0-discovery.md` нет — Day 1 выполнен по плану, корректировки касаются только гипотез для Фазы 1 (это всё равно решается в Day 5/6).
