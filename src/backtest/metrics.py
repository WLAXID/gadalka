"""Метрики бэктеста: EV, Sharpe, max DD, win rate, и т.д."""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_metrics(trades: pd.DataFrame) -> dict:
    """Сводные метрики по DataFrame сделок (output of execute_strategy)."""
    if len(trades) == 0:
        return {
            "n_trades": 0,
            "win_rate": None,
            "avg_buy_cost": None,
            "ev_per_bet": None,
            "ev_per_dollar": None,
            "total_pnl": 0.0,
            "sharpe": None,
            "max_dd": None,
            "max_dd_pct": None,
        }

    n = len(trades)
    win_rate = float(trades["resolved_yes"].mean())
    avg_buy_cost = float(trades["buy_cost"].mean())
    ev_per_bet = float(trades["pnl"].mean())
    # EV per $1 invested
    ev_per_dollar = float(trades["pnl"].sum() / trades["buy_cost"].sum())

    total_pnl = float(trades["pnl"].sum())

    # Sharpe (по сделкам, не времени — приближённо)
    if n > 1:
        std = float(trades["pnl"].std())
        sharpe = ev_per_bet / std * np.sqrt(n) if std > 1e-9 else None
    else:
        sharpe = None

    # Max drawdown по cumulative pnl
    cum = trades["pnl"].cumsum()
    running_max = cum.cummax()
    drawdown = cum - running_max
    max_dd = float(drawdown.min())
    # DD as fraction of running_max при размере 1$/bet
    invested_so_far = trades["buy_cost"].cumsum()
    dd_pct = (drawdown / invested_so_far.replace(0, np.nan)).min()
    max_dd_pct = float(dd_pct) if pd.notna(dd_pct) else None

    return {
        "n_trades": n,
        "win_rate": round(win_rate, 4),
        "avg_buy_cost": round(avg_buy_cost, 4),
        "ev_per_bet": round(ev_per_bet, 4),
        "ev_per_dollar": round(ev_per_dollar, 4),
        "total_pnl": round(total_pnl, 2),
        "sharpe": round(sharpe, 2) if sharpe is not None else None,
        "max_dd": round(max_dd, 2),
        "max_dd_pct": round(max_dd_pct, 4) if max_dd_pct is not None else None,
    }


def kelly_fraction(win_rate: float, win_amount: float, loss_amount: float) -> float:
    """Optimal Kelly fraction для двухисходовой ставки.

    f* = (p*b - q) / b, где b = win/loss, p = win_rate, q = 1-p.
    """
    if loss_amount <= 0:
        return 0.0
    b = win_amount / loss_amount
    p = win_rate
    q = 1 - p
    if b == 0:
        return 0.0
    f = (p * b - q) / b
    return max(0.0, min(1.0, f))
