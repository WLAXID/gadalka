"""Sanity checks для wide backtest findings — БЕЗ розовых очков.

4 проверки:
1. Правильный Sharpe (по дневным PnL, не по сделкам)
2. Survivorship — сколько рынков с past-endDate так и не резолвнулись
3. EV по категориям/тегам — равномерен ли edge
4. Time-based train/test split — out-of-sample на свежих данных

Запуск::

    python scripts/sanity_checks.py
"""
from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import duckdb  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from loguru import logger  # noqa: E402

from src.backtest.costs import CostModel  # noqa: E402


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(
        f"CREATE VIEW markets AS SELECT * FROM "
        f"read_parquet('{ROOT}/data/raw/markets_*.parquet', union_by_name=true)"
    )
    full_dir = ROOT / "data" / "raw" / "prices_history_full"
    if full_dir.exists() and any(full_dir.glob("*.parquet")):
        con.execute(
            f"""CREATE VIEW prices_history AS
            SELECT condition_id, token_id, outcome, t, p FROM
              read_parquet('{ROOT}/data/raw/prices_history/*.parquet',
                           union_by_name=true)
            UNION
            SELECT condition_id, token_id, outcome, t, p FROM
              read_parquet('{ROOT}/data/raw/prices_history_full/*.parquet',
                           union_by_name=true)
            """
        )
    else:
        con.execute(
            f"CREATE VIEW prices_history AS SELECT * FROM "
            f"read_parquet('{ROOT}/data/raw/prices_history/*.parquet', "
            f"union_by_name=true)"
        )
    return con


def section(title: str) -> None:
    print(f"\n{'='*70}\n  {title}\n{'='*70}")


def get_trades_h1_at_horizon(
    con: duckdb.DuckDBPyConnection,
    horizon_hours: int = 24,
    low: float = 0.50,
    high: float = 0.85,
    min_volume: float = 10_000.0,
    cost: CostModel = CostModel.realistic(),
) -> pd.DataFrame:
    """Вернуть trades DataFrame для H1 baseline на выбранном horizon."""
    secs = horizon_hours * 3600
    q = f"""
    WITH market_close AS (
      SELECT
        m.conditionId AS condition_id,
        m.token_id_yes,
        CAST(m.resolved_yes AS BOOL) AS resolved_yes,
        CAST(m.volumeNum AS DOUBLE)  AS volume,
        CAST(MAX(p.t) AS BIGINT)     AS close_ts
      FROM markets m
      JOIN prices_history p ON p.condition_id = m.conditionId
      WHERE m.token_id_yes IS NOT NULL
        AND m.resolved_yes IS NOT NULL
      GROUP BY 1, 2, 3, 4
    ),
    entry_price AS (
      SELECT mc.condition_id, mc.token_id_yes, mc.resolved_yes,
             mc.volume, mc.close_ts, p.p AS entry_price
      FROM market_close mc
      ASOF JOIN prices_history p
        ON p.token_id = mc.token_id_yes
        AND p.t <= mc.close_ts - {secs}
    )
    SELECT condition_id, resolved_yes, volume, close_ts, entry_price
    FROM entry_price
    WHERE entry_price IS NOT NULL
      AND entry_price >= {low}
      AND entry_price < {high}
      AND volume >= {min_volume}
    ORDER BY close_ts
    """
    df = con.execute(q).df()
    df["buy_cost"] = df["entry_price"].apply(cost.effective_buy_price)
    df["payout"] = df["resolved_yes"].astype(int).astype(float)
    df["pnl"] = df.apply(
        lambda r: cost.realize_pnl(r["buy_cost"], r["payout"]), axis=1
    )
    df["close_date"] = pd.to_datetime(df["close_ts"], unit="s").dt.date
    return df


def check_1_proper_sharpe(con: duckdb.DuckDBPyConnection) -> None:
    section("CHECK 1 — Proper annualized Sharpe (daily, not per-trade)")
    print("""
Старый расчёт: sqrt(n_trades) × mean_pnl/std_pnl
  → завышен потому что n_trades растёт без bound (надо sqrt(252) для годового)

Корректный: считаем дневные PnL, потом sqrt(252) × mean_daily/std_daily
""")

    rows = []
    for h in [3, 24, 168]:  # T-3h, T-1d, T-7d
        df = get_trades_h1_at_horizon(con, horizon_hours=h)
        if len(df) == 0:
            continue
        # OLD Sharpe (per-trade)
        n = len(df)
        mean_t = df["pnl"].mean()
        std_t = df["pnl"].std()
        sharpe_old = (mean_t / std_t) * np.sqrt(n) if std_t > 1e-9 else None

        # NEW Sharpe (daily, annualized)
        daily = df.groupby("close_date")["pnl"].sum()
        # сколько вложено в среднем за день (для нормализации PnL → return)
        daily_invested = df.groupby("close_date")["buy_cost"].sum()
        daily_return = (daily / daily_invested).fillna(0)
        if len(daily_return) > 1:
            mean_d = daily_return.mean()
            std_d = daily_return.std()
            sharpe_new = (mean_d / std_d) * np.sqrt(252) if std_d > 1e-9 else None
        else:
            sharpe_new = None

        rows.append({
            "horizon": f"T-{h}h" if h < 24 else f"T-{h//24}d",
            "n_trades": n,
            "n_days_with_trades": daily.shape[0],
            "ev_per_dollar": round(df["pnl"].sum() / df["buy_cost"].sum(), 4),
            "sharpe_OLD_per_trade": round(sharpe_old, 2) if sharpe_old else None,
            "sharpe_NEW_daily_annualized": round(sharpe_new, 2) if sharpe_new else None,
        })

    print(pd.DataFrame(rows).to_string(index=False))
    print("""
Интерпретация:
- Sharpe > 4 (annualized daily) — экстремально хорошо. 1-2 хорошо.
- Если NEW сильно меньше OLD — старый расчёт был артефактом.
""")


def check_2_survivorship(con: duckdb.DuckDBPyConnection) -> None:
    section("CHECK 2 — Survivorship / selection bias")
    print("""
Какой именно тип selection bias у нас? Markets parquet собран как
"closed=true". Но что значит "closed" на Polymarket?
""")

    q = """
    SELECT
      COUNT(*) AS total,
      COUNT(*) FILTER (WHERE try_cast(endDate AS TIMESTAMP) < CURRENT_TIMESTAMP) AS past_end,
      COUNT(*) FILTER (WHERE try_cast(endDate AS TIMESTAMP) >= CURRENT_TIMESTAMP) AS future_end,
      COUNT(*) FILTER (WHERE resolved_yes IS NULL) AS unresolved,
      COUNT(*) FILTER (WHERE CAST(closed AS BOOLEAN) = true AND resolved_yes IS NULL) AS closed_unresolved,
      COUNT(*) FILTER (WHERE CAST(closed AS BOOLEAN) = false) AS not_closed
    FROM markets
    """
    r = con.execute(q).fetchone()
    total, past_end, future_end, unresolved, closed_unresolved, not_closed = r
    print(f"Total markets в датасете:                  {total:>6,}")
    print(f"  endDate в прошлом (формально просрочен):  {past_end:>6,}  ({100*past_end/total:.1f}%)")
    print(f"  endDate в будущем (досрочно резолвнут?):  {future_end:>6,}  ({100*future_end/total:.1f}%)")
    print(f"  resolved_yes IS NULL (без резолва):       {unresolved:>6,}  ({100*unresolved/total:.1f}%)")
    print(f"  closed=true но без резолва (зависшие):    {closed_unresolved:>6,}  ({100*closed_unresolved/total:.1f}%)")
    print(f"  closed=false (активные):                   {not_closed:>6,}  ({100*not_closed/total:.1f}%)")
    print("""
ВАЖНО:
- Наш датасет = closed_unresolved + резолвнутые. Но что с активными?
- Если наш сборщик НЕ забрал ВСЕ "closed=true" рынки — выборка перекошена.
- Если 80%+ наших markets имеют endDate в БУДУЩЕМ — значит мы изучили
  только UMA-trigger early resolutions, а долгие медленные резолвы не вошли.
  Это своя популяция со своим bias.
""")


def check_3_by_category(con: duckdb.DuckDBPyConnection) -> None:
    section("CHECK 3 — Edge по категориям рынков")
    print("""
Если edge концентрируется в одной нише (например, спорт-фавориты) —
это не general phenomenon а узкая дыра. Live будет отличаться.
""")

    # Сделаем разрез по простой эвристике на основе question text
    df = get_trades_h1_at_horizon(con, horizon_hours=24)
    if len(df) == 0:
        print("Нет ставок при текущих фильтрах")
        return

    # Дотянем question/слаг к trades
    cids = "','".join(df["condition_id"].tolist())
    meta = con.execute(
        f"SELECT conditionId, question, slug FROM markets "
        f"WHERE conditionId IN ('{cids}')"
    ).df()
    df = df.merge(meta, left_on="condition_id", right_on="conditionId", how="left")

    def categorize(q: str) -> str:
        if not isinstance(q, str):
            return "z_unknown"
        ql = q.lower()
        if any(w in ql for w in ["nba", "nfl", "fifa", "world cup", "olympics",
                                  "champion", "uefa", "f1", "ufc", "wimbledon"]):
            return "a_sport"
        if any(w in ql for w in ["bitcoin", "btc", "ethereum", "eth", "crypto",
                                  "solana", "sol "]):
            return "b_crypto"
        if any(w in ql for w in ["trump", "biden", "election", "vote", "senate",
                                  "congress", "president", "putin"]):
            return "c_politics"
        if any(w in ql for w in ["weather", "hurricane", "temperature"]):
            return "d_weather"
        if any(w in ql for w in ["fed ", "interest rate", "cpi", "inflation",
                                  "gdp", "recession"]):
            return "e_economy"
        return "f_other"

    df["category"] = df["question"].apply(categorize)
    by_cat = df.groupby("category").agg(
        n_trades=("pnl", "size"),
        win_rate=("resolved_yes", "mean"),
        ev_per_bet=("pnl", "mean"),
        ev_per_dollar=("pnl", lambda x: x.sum() / df.loc[x.index, "buy_cost"].sum()),
        total_pnl=("pnl", "sum"),
    ).round(4).sort_index()
    print(by_cat.to_string())
    print("""
Интерпретация:
- Если 80%+ ставок в одной категории — edge не diversified
- Если EV по категориям разнится в 5+ раз — есть концентрация
- Категория «other» обычно > 50% — нормально, эвристика грубая
""")


def check_4_recent_oos(con: duckdb.DuckDBPyConnection) -> None:
    section("CHECK 4 — Out-of-sample на свежих 30 днях")
    print("""
Отрезаем последние 30 дней как «test», остальное «train». Если в test
EV сильно отличается от train — стратегия не воспроизводится во времени.
""")

    df = get_trades_h1_at_horizon(con, horizon_hours=24)
    if len(df) < 50:
        print("Слишком мало ставок для OOS")
        return

    cutoff_ts = int(df["close_ts"].max() - 30 * 86400)
    train = df[df["close_ts"] < cutoff_ts]
    test = df[df["close_ts"] >= cutoff_ts]

    def stats(d: pd.DataFrame, label: str) -> None:
        if len(d) == 0:
            print(f"{label}: empty")
            return
        ev_dollar = d["pnl"].sum() / d["buy_cost"].sum()
        win = d["resolved_yes"].mean()
        # daily Sharpe
        daily = d.groupby("close_date")[["pnl", "buy_cost"]].sum()
        daily["ret"] = daily["pnl"] / daily["buy_cost"]
        if len(daily) > 1 and daily["ret"].std() > 1e-9:
            sharpe = daily["ret"].mean() / daily["ret"].std() * np.sqrt(252)
        else:
            sharpe = None
        period_start = pd.to_datetime(d["close_ts"].min(), unit="s").date()
        period_end = pd.to_datetime(d["close_ts"].max(), unit="s").date()
        sh_str = f"{sharpe:.2f}" if sharpe is not None else "NA"
        print(f"{label:>6}: n={len(d):>5}  win={win:.3f}  "
              f"EV/$={ev_dollar:+.4f}  sharpe={sh_str}  "
              f"({period_start} → {period_end})")

    stats(train, "TRAIN")
    stats(test, "TEST")
    print("""
Интерпретация:
- TEST EV/$ близок к TRAIN (в 2x) — edge воспроизводится
- TEST EV/$ значимо ниже — edge размывается (режимный сдвиг)
- TEST EV/$ отрицательный — edge мёртв на свежем периоде
""")


def check_5_pessimistic_costs(con: duckdb.DuckDBPyConnection) -> None:
    section("CHECK 5 — Edge при pessimistic costs")
    print("""
Realistic: spread=1.5%, slippage=0.5%. На low-volume рынках это
оптимистично — реальный спред 3-5%, slippage 1-2% даже на $1 ставке.
Что останется от edge если взять pessimistic?
""")
    rows = []
    for cost_label, cost in [
        ("optimistic", CostModel.optimistic()),
        ("realistic", CostModel.realistic()),
        ("pessimistic", CostModel.pessimistic()),
        ("worst (5% spread, 2% slip)", CostModel(fee_rate=0.02, spread_pct=0.05, slippage_pct=0.02)),
    ]:
        df = get_trades_h1_at_horizon(con, horizon_hours=24, cost=cost)
        if len(df) == 0:
            continue
        ev_dollar = df["pnl"].sum() / df["buy_cost"].sum()
        daily = df.groupby("close_date")[["pnl", "buy_cost"]].sum()
        daily["ret"] = daily["pnl"] / daily["buy_cost"]
        sharpe = (daily["ret"].mean() / daily["ret"].std() * np.sqrt(252)
                  if daily["ret"].std() > 1e-9 else None)
        rows.append({
            "cost_model": cost_label,
            "spread_pct": cost.spread_pct,
            "slippage_pct": cost.slippage_pct,
            "n_trades": len(df),
            "ev_per_dollar": round(ev_dollar, 4),
            "sharpe_daily_annualized": round(sharpe, 2) if sharpe else None,
        })
    print(pd.DataFrame(rows).to_string(index=False))
    print("""
Интерпретация:
- Если worst case даёт EV/$ < 0 — edge нежизнеспособен в low-volume
- Если все варианты в плюсе — стратегия выдерживает любые costs
""")


def main() -> None:
    con = connect()
    pd.set_option("display.width", 180)
    pd.set_option("display.max_columns", 30)

    check_1_proper_sharpe(con)
    check_2_survivorship(con)
    check_3_by_category(con)
    check_4_recent_oos(con)
    check_5_pessimistic_costs(con)

    print("\n" + "="*70)
    print("  ИТОГ")
    print("="*70)
    print("""
Все findings выше — для H1 [0.50, 0.85] с min_volume=$10k на T-24h.
Чем хуже эти sanity checks — тем меньше доверия к paper-цифрам в backtest
findings. Если в TEST EV/$ -2% или Sharpe < 1 — это серьёзный сигнал.
""")


if __name__ == "__main__":
    main()
