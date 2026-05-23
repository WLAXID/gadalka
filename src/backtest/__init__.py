"""Backtest framework для gadalka — Фаза 1."""

from src.backtest.costs import CostModel
from src.backtest.dataset import build_backtest_dataset
from src.backtest.engine import execute_strategy
from src.backtest.metrics import compute_metrics
from src.backtest.strategies import (
    BuyFavoriteStrategy,
    LogisticBaselineStrategy,
    Strategy,
)

__all__ = [
    "build_backtest_dataset",
    "execute_strategy",
    "compute_metrics",
    "CostModel",
    "Strategy",
    "BuyFavoriteStrategy",
    "LogisticBaselineStrategy",
]
