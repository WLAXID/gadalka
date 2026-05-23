"""Исполнение стратегии: применяем CostModel к выбранным сделкам."""

from __future__ import annotations

import pandas as pd

from src.backtest.costs import CostModel
from src.backtest.strategies import Strategy


def execute_strategy(
    df: pd.DataFrame,
    strategy: Strategy,
    costs: CostModel,
) -> pd.DataFrame:
    """Выполнить стратегию на df и вернуть DataFrame со сделками + P&L.

    Каждая строка результата — один трейд:
      condition_id, entry_price, buy_cost, payout, pnl, resolved_yes, close_ts, volume
    """
    selected = strategy.select(df)
    if len(selected) == 0:
        return pd.DataFrame(
            columns=[
                "condition_id", "entry_price", "buy_cost", "payout",
                "pnl", "resolved_yes", "close_ts", "volume", "strategy",
            ]
        )

    selected = selected.copy()
    selected["buy_cost"] = selected["entry_price"].apply(costs.effective_buy_price)
    selected["payout"] = selected["resolved_yes"].astype(int).astype(float)
    selected["pnl"] = selected.apply(
        lambda r: costs.realize_pnl(r["buy_cost"], r["payout"]), axis=1
    )

    cols = [
        "condition_id", "entry_price", "buy_cost", "payout",
        "pnl", "resolved_yes", "close_ts", "volume", "strategy",
    ]
    return selected[cols].sort_values("close_ts").reset_index(drop=True)
