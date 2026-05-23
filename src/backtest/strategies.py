"""Стратегии для бэктеста.

Все стратегии принимают на вход DataFrame с фичами и возвращают boolean Series
(True = берём сделку), плюс колонку 'entry_price' — цена YES в момент входа.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


class Strategy:
    """Базовый интерфейс."""

    name: str = "base"

    def select(self, df: pd.DataFrame) -> pd.DataFrame:
        """Вернёт сабсет df со столбцом entry_price."""
        raise NotImplementedError


@dataclass
class BuyFavoriteStrategy(Strategy):
    """H1 baseline: купить YES когда price_yes_t24h ∈ [low, high]."""

    low: float = 0.50
    high: float = 0.85
    horizon: str = "t24h"  # t1h / t6h / t24h / t7d
    min_volume: float | None = None
    max_volume: float | None = None
    require_stable: bool = False
    # if True: signal должен подтверждаться и в T-7d (multi-horizon ensemble)

    @property
    def name(self) -> str:
        parts = [f"H1[{self.low:.2f}-{self.high:.2f}]@{self.horizon}"]
        if self.min_volume:
            parts.append(f"v>={self.min_volume:g}")
        if self.max_volume:
            parts.append(f"v<={self.max_volume:g}")
        if self.require_stable:
            parts.append("stable")
        return " ".join(parts)

    def select(self, df: pd.DataFrame) -> pd.DataFrame:
        price_col = f"price_yes_{self.horizon}"
        mask = (
            df[price_col].notna()
            & (df[price_col] >= self.low)
            & (df[price_col] < self.high)
        )
        if self.min_volume is not None:
            mask &= df["volume"] >= self.min_volume
        if self.max_volume is not None:
            mask &= df["volume"] <= self.max_volume
        if self.require_stable:
            # signal должен быть в [low, high] И на T-7d, И на текущем horizon
            mask &= (
                df["price_yes_t7d"].notna()
                & (df["price_yes_t7d"] >= self.low)
                & (df["price_yes_t7d"] < self.high)
            )
        out = df[mask].copy()
        out["entry_price"] = out[price_col]
        out["strategy"] = self.name
        return out


@dataclass
class LogisticBaselineStrategy(Strategy):
    """H2: logistic regression на market features. Обучается на train, применяется на test."""

    feature_cols: tuple[str, ...] = (
        "price_yes_t24h",
        "price_yes_t7d",
        "volume",
        "lifetime_days",
        "n_history_points",
    )
    threshold: float = 0.05
    # entry: если P_model(yes) - price_yes_t24h > threshold

    def __init__(self, **kwargs):
        self.feature_cols = kwargs.get("feature_cols", self.feature_cols)
        self.threshold = kwargs.get("threshold", self.threshold)
        self._model = None
        self._feat_scaler = None

    @property
    def name(self) -> str:
        return f"H2[LR thr={self.threshold:.2f}]"

    def fit(self, df_train: pd.DataFrame) -> None:
        """Обучить модель на train."""
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler

        X = df_train[list(self.feature_cols)].copy()
        # Заполним NaN медианой train, чтобы не терять строки
        X = X.fillna(X.median(numeric_only=True))
        y = df_train["resolved_yes"].astype(int)

        self._feat_scaler = StandardScaler().fit(X)
        X_scaled = self._feat_scaler.transform(X)
        self._model = LogisticRegression(max_iter=1000, C=1.0).fit(X_scaled, y)

    def select(self, df: pd.DataFrame) -> pd.DataFrame:
        if self._model is None:
            raise RuntimeError("Сначала fit() на train")
        X = df[list(self.feature_cols)].copy()
        X = X.fillna(X.median(numeric_only=True))
        X_scaled = self._feat_scaler.transform(X)
        p_pred = self._model.predict_proba(X_scaled)[:, 1]
        df = df.copy()
        df["p_model"] = p_pred
        df["entry_price"] = df["price_yes_t24h"]
        df["edge"] = df["p_model"] - df["entry_price"]
        mask = (
            df["entry_price"].notna()
            & (df["edge"] >= self.threshold)
            & (df["entry_price"] >= 0.05)  # не лезем в самые экстремальные longshots
            & (df["entry_price"] <= 0.95)
        )
        out = df[mask].copy()
        out["strategy"] = self.name
        return out
