"""Runner всех вариантов H1 / H2 на train+test.

Запуск::

    python scripts/run_backtest.py

Результаты:
- stdout: таблицы метрик на train, test, rolling
- data/processed/backtest_trades.parquet — все сделки финального H1 baseline
- data/processed/backtest_report.json — JSON со всеми метриками
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from src.backtest import (  # noqa: E402
    BuyFavoriteStrategy,
    CostModel,
    LogisticBaselineStrategy,
    Strategy,
    compute_metrics,
    execute_strategy,
)
from src.backtest.dataset import build_backtest_dataset, time_train_test_split  # noqa: E402


def section(title: str) -> None:
    print(f"\n{'━' * 4} {title} {'━' * (62 - len(title))}")


def run_one(
    df: pd.DataFrame,
    strategy: Strategy,
    cost: CostModel,
    *,
    cost_label: str = "realistic",
) -> dict:
    """Прогнать одну стратегию + cost-модель, вернуть метрики."""
    trades = execute_strategy(df, strategy, cost)
    metrics = compute_metrics(trades)
    metrics["strategy"] = strategy.name
    metrics["cost"] = cost_label
    return metrics


def main() -> None:
    pd.set_option("display.width", 160)
    pd.set_option("display.max_columns", 30)

    # ============================================================
    # 1) Загрузка датасета
    # ============================================================
    section("Загрузка датасета")
    df = build_backtest_dataset(require_t24h=True)
    print(f"Анализируем {len(df)} рынков (с T-24h покрытием)")
    print(f"Период: "
          f"{pd.to_datetime(df['close_ts'].min(), unit='s').date()} → "
          f"{pd.to_datetime(df['close_ts'].max(), unit='s').date()}")

    # ============================================================
    # 2) Train / test split
    # ============================================================
    section("Train/test split (oldest 70% / newest 30%)")
    train, test = time_train_test_split(df, train_frac=0.7)
    print(f"Train: {len(train)} markets, Test: {len(test)} markets")

    # ============================================================
    # 3) Cost models — три сценария
    # ============================================================
    costs = {
        "optimistic":  CostModel.optimistic(),
        "realistic":   CostModel.realistic(),
        "pessimistic": CostModel.pessimistic(),
    }

    # ============================================================
    # 4) Стратегии H1 + варианты
    # ============================================================
    strategies: list[Strategy] = [
        # H1 baseline
        BuyFavoriteStrategy(low=0.50, high=0.85, horizon="t24h"),
        # H1.a — mid-volume filter
        BuyFavoriteStrategy(low=0.50, high=0.85, horizon="t24h",
                            min_volume=10_000, max_volume=100_000),
        # H1.b — tight band
        BuyFavoriteStrategy(low=0.65, high=0.85, horizon="t24h"),
        # H1.c — multi-horizon ensemble
        BuyFavoriteStrategy(low=0.50, high=0.85, horizon="t24h",
                            require_stable=True),
        # Параметрический grid: low=0.55, 0.60
        BuyFavoriteStrategy(low=0.55, high=0.90, horizon="t24h"),
        BuyFavoriteStrategy(low=0.60, high=0.90, horizon="t24h"),
        # Узкий high-fav диапазон
        BuyFavoriteStrategy(low=0.70, high=0.85, horizon="t24h"),
        BuyFavoriteStrategy(low=0.70, high=0.85, horizon="t24h",
                            min_volume=10_000, max_volume=100_000),
    ]

    # ============================================================
    # 5) Прогон на train — sensitivity
    # ============================================================
    section("Train — все варианты × все cost-модели")
    train_rows = []
    for strat in strategies:
        for cost_label, cost in costs.items():
            m = run_one(train, strat, cost, cost_label=cost_label)
            train_rows.append(m)
    train_metrics = pd.DataFrame(train_rows)
    print(train_metrics.to_string(index=False))

    # ============================================================
    # 6) Прогон на test (OOS)
    # ============================================================
    section("Test (OOS) — те же варианты × cost-модели")
    test_rows = []
    for strat in strategies:
        for cost_label, cost in costs.items():
            m = run_one(test, strat, cost, cost_label=cost_label)
            test_rows.append(m)
    test_metrics = pd.DataFrame(test_rows)
    print(test_metrics.to_string(index=False))

    # ============================================================
    # 7) H2 — Logistic regression baseline
    # ============================================================
    section("H2 — Logistic regression baseline")
    h2 = LogisticBaselineStrategy(threshold=0.05)
    h2.fit(train)

    h2_results = []
    for split_name, split_df in [("train", train), ("test", test)]:
        for cost_label, cost in costs.items():
            m = run_one(split_df, h2, cost, cost_label=cost_label)
            m["split"] = split_name
            h2_results.append(m)
    h2_metrics = pd.DataFrame(h2_results)
    print(h2_metrics.to_string(index=False))

    # ============================================================
    # 8) Rolling walk-forward (4 окна)
    # ============================================================
    section("Rolling walk-forward на H1 baseline (4 окна)")
    df_sorted = df.sort_values("close_ts").reset_index(drop=True)
    rolling_rows = []
    h1_strategy = BuyFavoriteStrategy(low=0.50, high=0.85, horizon="t24h")
    n_windows = 4
    window_size = len(df_sorted) // n_windows
    for i in range(n_windows):
        a = i * window_size
        b = (i + 1) * window_size if i < n_windows - 1 else len(df_sorted)
        window = df_sorted.iloc[a:b]
        m = run_one(window, h1_strategy, CostModel.realistic(), cost_label="realistic")
        m["window"] = f"W{i+1}: {pd.to_datetime(window['close_ts'].min(), unit='s').date()} → {pd.to_datetime(window['close_ts'].max(), unit='s').date()}"
        m["n_in_window"] = len(window)
        rolling_rows.append(m)
    rolling_df = pd.DataFrame(rolling_rows)
    cols_show = ["window", "n_in_window", "n_trades", "win_rate",
                 "ev_per_bet", "ev_per_dollar", "total_pnl", "sharpe", "max_dd"]
    print(rolling_df[cols_show].to_string(index=False))

    # ============================================================
    # 9) Сохранить полный отчёт
    # ============================================================
    out_dir = ROOT / "data" / "processed"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Финальный H1 baseline на test — сохраняем сделки
    trades_h1_test = execute_strategy(test, strategies[0], CostModel.realistic())
    trades_h1_test.to_parquet(out_dir / "backtest_h1_test_trades.parquet", index=False)

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "n_markets_total": len(df),
        "n_train": len(train),
        "n_test": len(test),
        "train_metrics": train_metrics.to_dict("records"),
        "test_metrics": test_metrics.to_dict("records"),
        "h2_metrics": h2_metrics.to_dict("records"),
        "rolling_metrics": rolling_df.to_dict("records"),
    }
    (out_dir / "backtest_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(f"\n💾 Saved: {out_dir / 'backtest_report.json'}")
    print(f"💾 Saved: {out_dir / 'backtest_h1_test_trades.parquet'}")


if __name__ == "__main__":
    main()
