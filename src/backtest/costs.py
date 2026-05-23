"""Модель издержек для бэктеста.

Учитываем:
- Polymarket fee (2% от прибыли по умолчанию)
- Спред (входим по ask = mid + spread/2)
- Slippage (дополнительная просадка при движении рынка)

Все параметры fee/spread настраиваются.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    """Стандартная модель издержек для Polymarket."""

    fee_rate: float = 0.02       # 2% от прибыли (только при выигрыше)
    spread_pct: float = 0.015    # 1.5% спред — реалистично для mid-vol
    slippage_pct: float = 0.0    # доп. slippage (пока 0, добавим при large size)

    @classmethod
    def optimistic(cls) -> "CostModel":
        """Без спреда, только fee — теоретический upper bound."""
        return cls(fee_rate=0.02, spread_pct=0.0, slippage_pct=0.0)

    @classmethod
    def realistic(cls) -> "CostModel":
        """Реалистичная модель для mid-vol."""
        return cls(fee_rate=0.02, spread_pct=0.015, slippage_pct=0.005)

    @classmethod
    def pessimistic(cls) -> "CostModel":
        """Консервативная — для low-vol или плохих условий."""
        return cls(fee_rate=0.02, spread_pct=0.03, slippage_pct=0.01)

    def effective_buy_price(self, mid_price: float) -> float:
        """Реальная цена покупки = mid + half spread + slippage."""
        return mid_price * (1 + self.spread_pct / 2 + self.slippage_pct)

    def realize_pnl(self, buy_cost: float, payout: float) -> float:
        """Чистый P&L от сделки с учётом fee.

        - buy_cost: фактическая цена покупки (выход CashFlow)
        - payout: 1.0 если резолв YES, 0.0 если NO
        Возвращает P&L (может быть отрицательным).
        """
        profit_pretax = payout - buy_cost
        fee = max(0.0, profit_pretax) * self.fee_rate
        return profit_pretax - fee
