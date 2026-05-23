"""EDA датасета — Day 5 Фазы 0.

Запускается из корня проекта::

    python scripts/eda_dataset.py

Считает:
- Распределения (volume, life-time, end-price)
- Longshot strategy sanity check (buy YES at T-24h при разных ценах)
- Favorite strategy sanity check (buy NO когда YES >= 0.90)
- Стратифицированный анализ по volume buckets
- EV per bet с учётом 2% Polymarket fee
"""

from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from src.storage.duckdb_loader import GadalkaDB  # noqa: E402


# Polymarket fee = 2% от прибыли (применяется только когда выигрываем)
FEE_RATE = 0.02


def section(title: str) -> None:
    print(f"\n{'━' * 4} {title} {'━' * (60 - len(title))}")


def main() -> None:
    pd.set_option("display.width", 140)
    pd.set_option("display.max_columns", 20)
    pd.set_option("display.float_format", "{:.4f}".format)

    with GadalkaDB(ROOT / "data" / "processed" / "gadalka.duckdb") as db:
        db.register_parquet_views(root=ROOT)

        # ============================================================
        # Step 0 — материализуем анализируемый view
        # ============================================================

        db.sql(
            """
            CREATE OR REPLACE TEMP VIEW market_close AS
            SELECT
              m.conditionId AS condition_id,
              m.token_id_yes,
              m.token_id_no,
              CAST(m.resolved_yes AS BOOL) AS resolved_yes,
              CAST(m.final_price_yes AS DOUBLE) AS final_price_yes,
              CAST(m.volumeNum AS DOUBLE) AS volume,
              CAST(MAX(p.t) AS BIGINT) AS close_ts,
              CAST(MIN(p.t) AS BIGINT) AS open_ts,
              COUNT(p.t) AS n_history_points
            FROM markets m
            JOIN prices_history p ON p.condition_id = m.conditionId
            WHERE m.token_id_yes IS NOT NULL
              AND m.resolved_yes IS NOT NULL
            GROUP BY 1, 2, 3, 4, 5, 6
            """
        )

        section("Базовая статистика closed markets с историей")
        df = db.df(
            """
            SELECT
              COUNT(*) AS n_markets,
              SUM(CAST(resolved_yes AS INT)) AS resolved_yes,
              AVG(CAST(resolved_yes AS DOUBLE)) AS pct_yes,
              ROUND(AVG((close_ts - open_ts) / 86400.0), 1) AS avg_life_days,
              ROUND(MEDIAN((close_ts - open_ts) / 86400.0), 1) AS median_life_days,
              ROUND(AVG(n_history_points), 0) AS avg_points,
              ROUND(MEDIAN(n_history_points), 0) AS median_points
            FROM market_close
            """
        )
        print(df.to_string(index=False))

        # ============================================================
        # Step 1 — Time-to-resolve distribution
        # ============================================================

        section("Распределение времени жизни рынка (дни)")
        df = db.df(
            """
            WITH life AS (
              SELECT (close_ts - open_ts) / 86400.0 AS days
              FROM market_close
            )
            SELECT
              COUNT(*) AS n,
              ROUND(MIN(days), 1) AS min,
              ROUND(QUANTILE_CONT(days, 0.25), 1) AS p25,
              ROUND(QUANTILE_CONT(days, 0.50), 1) AS median,
              ROUND(QUANTILE_CONT(days, 0.75), 1) AS p75,
              ROUND(QUANTILE_CONT(days, 0.90), 1) AS p90,
              ROUND(QUANTILE_CONT(days, 0.99), 1) AS p99,
              ROUND(MAX(days), 1) AS max
            FROM life
            """
        )
        print(df.to_string(index=False))

        # ============================================================
        # Step 2 — End-price distribution (favorite-longshot гистограмма)
        # ============================================================

        section("Распределение final_price_yes (резолв-bias)")
        df = db.df(
            """
            SELECT
              CASE
                WHEN final_price_yes < 0.01 THEN 'a [0.00-0.01)'
                WHEN final_price_yes < 0.05 THEN 'b [0.01-0.05)'
                WHEN final_price_yes < 0.10 THEN 'c [0.05-0.10)'
                WHEN final_price_yes < 0.30 THEN 'd [0.10-0.30)'
                WHEN final_price_yes < 0.50 THEN 'e [0.30-0.50)'
                WHEN final_price_yes < 0.70 THEN 'f [0.50-0.70)'
                WHEN final_price_yes < 0.90 THEN 'g [0.70-0.90)'
                WHEN final_price_yes < 0.95 THEN 'h [0.90-0.95)'
                WHEN final_price_yes < 0.99 THEN 'i [0.95-0.99)'
                ELSE 'j [0.99-1.00]'
              END AS bucket,
              COUNT(*) AS n
            FROM market_close
            GROUP BY 1
            ORDER BY 1
            """
        )
        print(df.to_string(index=False))

        # ============================================================
        # Step 3 — Price at T-24h (через ASOF JOIN)
        # ============================================================

        # Строим вью: цена YES за T-24h до резолва, для каждого рынка
        db.sql(
            """
            CREATE OR REPLACE TEMP VIEW price_at_t24h AS
            SELECT
              mc.condition_id,
              mc.resolved_yes,
              mc.volume,
              mc.close_ts,
              p.p AS price_yes_t24h
            FROM market_close mc
            ASOF JOIN prices_history p
              ON p.token_id = mc.token_id_yes
              AND p.t <= mc.close_ts - 86400
            WHERE p.p IS NOT NULL
            """
        )

        section("Покрытие выборки для T-24h анализа")
        df = db.df(
            """
            SELECT
              COUNT(*) AS n_with_t24h,
              ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM market_close), 1) AS pct_coverage
            FROM price_at_t24h
            """
        )
        print(df.to_string(index=False))

        # ============================================================
        # Step 4 — Longshot strategy: купить YES когда YES дёшев
        # ============================================================

        section("Longshot YES strategy — buy YES at T-24h, hold to resolve")
        print("EV формула: win_rate * (1-avg_price)*0.98 - (1-win_rate)*avg_price")
        print("(2% fee на прибыль, проигрыш = вся ставка)\n")

        df = db.df(
            f"""
            WITH bucketed AS (
              SELECT
                CASE
                  WHEN price_yes_t24h < 0.02 THEN '0  [0.000-0.020)'
                  WHEN price_yes_t24h < 0.05 THEN '1  [0.020-0.050)'
                  WHEN price_yes_t24h < 0.10 THEN '2  [0.050-0.100)'
                  WHEN price_yes_t24h < 0.15 THEN '3  [0.100-0.150)'
                  WHEN price_yes_t24h < 0.20 THEN '4  [0.150-0.200)'
                  WHEN price_yes_t24h < 0.30 THEN '5  [0.200-0.300)'
                  WHEN price_yes_t24h < 0.50 THEN '6  [0.300-0.500)'
                  WHEN price_yes_t24h < 0.70 THEN '7  [0.500-0.700)'
                  WHEN price_yes_t24h < 0.85 THEN '8  [0.700-0.850)'
                  WHEN price_yes_t24h < 0.95 THEN '9  [0.850-0.950)'
                  ELSE                              'A  [0.950-1.000]'
                END AS bucket,
                price_yes_t24h,
                resolved_yes
              FROM price_at_t24h
            )
            SELECT
              bucket,
              COUNT(*) AS n,
              ROUND(AVG(price_yes_t24h), 4) AS avg_price,
              ROUND(AVG(CAST(resolved_yes AS DOUBLE)), 4) AS win_rate,
              ROUND(AVG(CAST(resolved_yes AS DOUBLE)) - AVG(price_yes_t24h), 4) AS edge,
              ROUND(
                AVG(CAST(resolved_yes AS DOUBLE)) * (1 - AVG(price_yes_t24h)) * (1 - {FEE_RATE})
                - (1 - AVG(CAST(resolved_yes AS DOUBLE))) * AVG(price_yes_t24h)
              , 4) AS ev_per_bet
            FROM bucketed
            GROUP BY bucket
            ORDER BY bucket
            """
        )
        print(df.to_string(index=False))

        # ============================================================
        # Step 5 — Favorite check: купить NO когда YES высок
        # ============================================================

        section("Favorite check — buy NO when YES at T-24h >= 0.90")
        print("Для YES≥0.90 (NO дёшев) считаем стратегию 'купить NO'.")
        print("win_rate_NO = (1 - resolved_yes), цена NO = 1 - price_yes.\n")
        df = db.df(
            f"""
            WITH favorite_yes AS (
              SELECT
                CASE
                  WHEN price_yes_t24h >= 0.99 THEN 'a [0.99-1.00]'
                  WHEN price_yes_t24h >= 0.95 THEN 'b [0.95-0.99)'
                  WHEN price_yes_t24h >= 0.90 THEN 'c [0.90-0.95)'
                  ELSE                              'd <0.90'
                END AS bucket,
                price_yes_t24h,
                resolved_yes
              FROM price_at_t24h
            )
            SELECT
              bucket,
              COUNT(*) AS n,
              ROUND(AVG(price_yes_t24h), 4) AS avg_yes_price,
              ROUND(1 - AVG(price_yes_t24h), 4) AS avg_no_price,
              ROUND(AVG(CAST(NOT resolved_yes AS DOUBLE)), 4) AS no_win_rate,
              ROUND(
                AVG(CAST(NOT resolved_yes AS DOUBLE))
                  * (1 - (1 - AVG(price_yes_t24h))) * (1 - {FEE_RATE})
                - (1 - AVG(CAST(NOT resolved_yes AS DOUBLE)))
                  * (1 - AVG(price_yes_t24h))
              , 4) AS ev_buy_no
            FROM favorite_yes
            GROUP BY bucket
            ORDER BY bucket
            """
        )
        print(df.to_string(index=False))

        # ============================================================
        # Step 6 — Longshot стратифицировано по volume bucket
        # ============================================================

        section("Longshot стратифицировано по volume (price_yes_t24h < 0.10)")
        df = db.df(
            f"""
            WITH cheap AS (
              SELECT
                CASE
                  WHEN volume >= 1000000  THEN 'a >$1M'
                  WHEN volume >= 100000   THEN 'b $100k-$1M'
                  WHEN volume >= 10000    THEN 'c $10k-$100k'
                  WHEN volume >= 1000     THEN 'd $1k-$10k'
                  WHEN volume >= 100      THEN 'e $100-$1k'
                  ELSE                          'f <$100'
                END AS vol_bucket,
                price_yes_t24h,
                resolved_yes
              FROM price_at_t24h
              WHERE price_yes_t24h < 0.10
            )
            SELECT
              vol_bucket,
              COUNT(*) AS n,
              ROUND(AVG(price_yes_t24h), 4) AS avg_price,
              ROUND(AVG(CAST(resolved_yes AS DOUBLE)), 4) AS win_rate,
              ROUND(
                AVG(CAST(resolved_yes AS DOUBLE)) * (1 - AVG(price_yes_t24h)) * (1 - {FEE_RATE})
                - (1 - AVG(CAST(resolved_yes AS DOUBLE))) * AVG(price_yes_t24h)
              , 4) AS ev_per_bet
            FROM cheap
            GROUP BY vol_bucket
            ORDER BY vol_bucket
            """
        )
        print(df.to_string(index=False))

        # ============================================================
        # Step 7 — Mid-volume рынки (main edge target)
        # ============================================================

        section("Mid-volume $10k-$100k — детальный longshot профиль")
        df = db.df(
            f"""
            WITH mid AS (
              SELECT *
              FROM price_at_t24h
              WHERE volume BETWEEN 10000 AND 100000
            ),
            bucketed AS (
              SELECT
                CASE
                  WHEN price_yes_t24h < 0.05 THEN '1  <0.05'
                  WHEN price_yes_t24h < 0.10 THEN '2  [0.05-0.10)'
                  WHEN price_yes_t24h < 0.15 THEN '3  [0.10-0.15)'
                  WHEN price_yes_t24h < 0.20 THEN '4  [0.15-0.20)'
                  WHEN price_yes_t24h < 0.30 THEN '5  [0.20-0.30)'
                  WHEN price_yes_t24h < 0.50 THEN '6  [0.30-0.50)'
                  WHEN price_yes_t24h < 0.70 THEN '7  [0.50-0.70)'
                  WHEN price_yes_t24h < 0.85 THEN '8  [0.70-0.85)'
                  WHEN price_yes_t24h < 0.95 THEN '9  [0.85-0.95)'
                  ELSE                              'A  >=0.95'
                END AS bucket,
                price_yes_t24h,
                resolved_yes
              FROM mid
            )
            SELECT
              bucket,
              COUNT(*) AS n,
              ROUND(AVG(price_yes_t24h), 4) AS avg_price,
              ROUND(AVG(CAST(resolved_yes AS DOUBLE)), 4) AS win_rate,
              ROUND(AVG(CAST(resolved_yes AS DOUBLE)) - AVG(price_yes_t24h), 4) AS edge,
              ROUND(
                AVG(CAST(resolved_yes AS DOUBLE)) * (1 - AVG(price_yes_t24h)) * (1 - {FEE_RATE})
                - (1 - AVG(CAST(resolved_yes AS DOUBLE))) * AVG(price_yes_t24h)
              , 4) AS ev_per_bet
            FROM bucketed
            GROUP BY bucket
            ORDER BY bucket
            """
        )
        print(df.to_string(index=False))

        # ============================================================
        # Step 8 — T-1h vs T-24h vs T-7d (динамика цены до резолва)
        # ============================================================

        for offset_label, offset_s in [
            ("T-1h", 3600),
            ("T-6h", 6 * 3600),
            ("T-24h", 86400),
            ("T-7d", 7 * 86400),
        ]:
            db.sql(
                f"""
                CREATE OR REPLACE TEMP VIEW price_at_{offset_label.replace('-', '_').replace('T_', 't_')} AS
                SELECT
                  mc.condition_id,
                  mc.resolved_yes,
                  mc.volume,
                  p.p AS price_yes
                FROM market_close mc
                ASOF JOIN prices_history p
                  ON p.token_id = mc.token_id_yes
                  AND p.t <= mc.close_ts - {offset_s}
                WHERE p.p IS NOT NULL
                """
            )

        section("Favorite @ T-1h / T-6h / T-24h / T-7d (price_yes 0.50-0.85)")
        # offset — reserved в SQL, используем horizon
        df = db.df(
            f"""
            WITH unioned AS (
              SELECT 'T-1h'  AS horizon, * FROM price_at_t_1h
                 WHERE price_yes BETWEEN 0.50 AND 0.85
              UNION ALL SELECT 'T-6h',  * FROM price_at_t_6h
                 WHERE price_yes BETWEEN 0.50 AND 0.85
              UNION ALL SELECT 'T-24h', * FROM price_at_t_24h
                 WHERE price_yes BETWEEN 0.50 AND 0.85
              UNION ALL SELECT 'T-7d',  * FROM price_at_t_7d
                 WHERE price_yes BETWEEN 0.50 AND 0.85
            )
            SELECT
              horizon,
              COUNT(*) AS n,
              ROUND(AVG(price_yes), 4) AS avg_price,
              ROUND(AVG(CAST(resolved_yes AS DOUBLE)), 4) AS win_rate,
              ROUND(
                AVG(CAST(resolved_yes AS DOUBLE)) * (1 - AVG(price_yes)) * (1 - {FEE_RATE})
                - (1 - AVG(CAST(resolved_yes AS DOUBLE))) * AVG(price_yes)
              , 4) AS ev_per_bet
            FROM unioned
            GROUP BY horizon
            ORDER BY
              CASE horizon
                WHEN 'T-1h' THEN 1 WHEN 'T-6h' THEN 2
                WHEN 'T-24h' THEN 3 WHEN 'T-7d' THEN 4
              END
            """
        )
        print(df.to_string(index=False))


if __name__ == "__main__":
    main()
