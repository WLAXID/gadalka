"""Sanity-check загруженного датасета: счётчики, распределения, целостность.

Запуск::

    python scripts/sanity_check_dataset.py
"""

from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.storage.duckdb_loader import GadalkaDB  # noqa: E402


def section(title: str) -> None:
    print(f"\n━━━ {title} ━━━")


def main() -> None:
    with GadalkaDB(ROOT / "data" / "processed" / "gadalka.duckdb") as db:
        db.register_parquet_views(root=ROOT)

        # ------------- Базовые счётчики -------------
        section("Базовые счётчики")
        df = db.df(
            """
            SELECT
              (SELECT COUNT(*) FROM markets)               AS n_markets,
              (SELECT COUNT(*) FROM prices_history)        AS n_price_points,
              (SELECT COUNT(DISTINCT condition_id) FROM prices_history)
                                                            AS n_markets_with_prices,
              (SELECT COUNT(DISTINCT token_id) FROM prices_history)
                                                            AS n_tokens_with_prices
            """
        )
        print(df.to_string(index=False))

        # ------------- Колонки в markets (диагностика) -------------
        section("Колонки в markets — первые 30")
        cols = db.df("DESCRIBE markets")
        print(cols.head(30).to_string(index=False))

        # ------------- Объёмы (распределение) -------------
        section("Распределение по объёму")
        df = db.df(
            """
            SELECT
              COUNT(*) FILTER (WHERE volumeNum >= 1000000) AS gt_1m,
              COUNT(*) FILTER (WHERE volumeNum >= 100000  AND volumeNum < 1000000) AS gt_100k,
              COUNT(*) FILTER (WHERE volumeNum >= 10000   AND volumeNum < 100000)  AS gt_10k,
              COUNT(*) FILTER (WHERE volumeNum >= 1000    AND volumeNum < 10000)   AS gt_1k,
              COUNT(*) FILTER (WHERE volumeNum >= 100     AND volumeNum < 1000)    AS gt_100,
              COUNT(*) FILTER (WHERE volumeNum < 100)                              AS lt_100,
              COUNT(*) FILTER (WHERE volumeNum IS NULL)                            AS null_vol
            FROM markets
            """
        )
        print(df.to_string(index=False))

        # ------------- Покрытие prices-history -------------
        section("Покрытие prices-history")
        df = db.df(
            """
            WITH market_coverage AS (
              SELECT
                m.conditionId AS condition_id,
                COUNT(p.t) AS n_points
              FROM markets m
              LEFT JOIN prices_history p ON p.condition_id = m.conditionId
              GROUP BY m.conditionId
            )
            SELECT
              COUNT(*)                                          AS total_markets,
              COUNT(*) FILTER (WHERE n_points = 0)              AS no_history,
              COUNT(*) FILTER (WHERE n_points BETWEEN 1 AND 10) AS very_few,
              COUNT(*) FILTER (WHERE n_points BETWEEN 11 AND 100) AS few,
              COUNT(*) FILTER (WHERE n_points BETWEEN 101 AND 1000) AS many,
              COUNT(*) FILTER (WHERE n_points > 1000)           AS lots
            FROM market_coverage
            """
        )
        print(df.to_string(index=False))

        # ------------- Резолв-исходы -------------
        section("Резолв-исходы (где известна финальная цена)")
        df = db.df(
            """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN resolved_yes = TRUE THEN 1 ELSE 0 END) AS resolved_yes,
              SUM(CASE WHEN resolved_yes = FALSE THEN 1 ELSE 0 END) AS resolved_no,
              SUM(CASE WHEN resolved_yes IS NULL THEN 1 ELSE 0 END) AS unknown,
              ROUND(100.0 * SUM(CASE WHEN resolved_yes = TRUE THEN 1 ELSE 0 END) /
                    NULLIF(COUNT(*) - SUM(CASE WHEN resolved_yes IS NULL THEN 1 ELSE 0 END), 0), 1)
                AS pct_yes
            FROM markets
            """
        )
        print(df.to_string(index=False))

        # ------------- Топ-рынков по volume -------------
        section("Топ-10 рынков по объёму")
        df = db.df(
            """
            SELECT
              SUBSTR(question, 1, 70) AS question,
              ROUND(volumeNum, 0) AS volume,
              resolved_yes,
              SUBSTR(endDate, 1, 10) AS end_date
            FROM markets
            ORDER BY volumeNum DESC NULLS LAST
            LIMIT 10
            """
        )
        print(df.to_string(index=False))

        # ------------- Почему 32% БЕЗ price-history? -------------
        section("Профиль рынков БЕЗ price-history")
        df = db.df(
            """
            WITH no_hist AS (
              SELECT m.*
              FROM markets m
              LEFT JOIN prices_history p ON p.condition_id = m.conditionId
              GROUP BY m.* HAVING COUNT(p.t) = 0
            )
            SELECT
              COUNT(*) AS n_total,
              COUNT(*) FILTER (WHERE token_id_yes IS NULL) AS no_token,
              COUNT(*) FILTER (WHERE enableOrderBook = FALSE) AS no_orderbook,
              COUNT(*) FILTER (WHERE volumeNum < 100) AS volume_lt_100,
              COUNT(*) FILTER (WHERE volumeNum > 10000) AS volume_gt_10k,
              ROUND(AVG(volumeNum), 1) AS avg_volume,
              MIN(SUBSTR(createdAt, 1, 10)) AS earliest_created,
              MAX(SUBSTR(createdAt, 1, 10)) AS latest_created
            FROM no_hist
            """
        )
        print(df.to_string(index=False))

        # ------------- Распределение по году создания -------------
        section("Рынки по году создания — покрытие price-history")
        df = db.df(
            """
            WITH per_market AS (
              SELECT
                SUBSTR(m.createdAt, 1, 4) AS year,
                m.conditionId,
                EXISTS(SELECT 1 FROM prices_history p WHERE p.condition_id = m.conditionId)
                  AS has_history
              FROM markets m
            )
            SELECT
              year,
              COUNT(*) AS n_markets,
              SUM(CAST(has_history AS INT)) AS with_hist,
              ROUND(100.0 * SUM(CAST(has_history AS INT)) / COUNT(*), 1) AS coverage_pct
            FROM per_market
            GROUP BY year
            ORDER BY year
            """
        )
        print(df.to_string(index=False))

        # ------------- Целостность токенов -------------
        section("Сверка token_id (Gamma vs prices_history)")
        df = db.df(
            """
            SELECT
              COUNT(*) AS markets_with_token_yes,
              SUM(CASE WHEN EXISTS(
                SELECT 1 FROM prices_history p
                WHERE p.token_id = m.token_id_yes
              ) THEN 1 ELSE 0 END) AS with_history
            FROM markets m
            WHERE m.token_id_yes IS NOT NULL
            """
        )
        print(df.to_string(index=False))

        # ------------- Sample случайных рынков с историей -------------
        section("Случайная выборка: 5 рынков и их история")
        df = db.df(
            """
            WITH sample AS (
              SELECT m.conditionId, m.question, m.token_id_yes,
                     m.volumeNum, m.resolved_yes
              FROM markets m
              WHERE m.token_id_yes IS NOT NULL AND m.volumeNum > 1000
              USING SAMPLE 5 ROWS
            )
            SELECT
              SUBSTR(s.question, 1, 50) AS question,
              ROUND(s.volumeNum, 0) AS volume,
              s.resolved_yes,
              COUNT(p.t) AS n_points,
              MIN(p.t) AS first_ts,
              MAX(p.t) AS last_ts,
              MIN(p.p) AS min_p,
              MAX(p.p) AS max_p
            FROM sample s
            LEFT JOIN prices_history p ON p.token_id = s.token_id_yes
            GROUP BY 1, 2, 3
            """
        )
        print(df.to_string(index=False))


if __name__ == "__main__":
    main()
