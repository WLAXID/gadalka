"""DuckDB-обёртка для аналитической работы с данными gadalka.

Структура:
- ``markets``         — metadata закрытых рынков (одна строка = один рынок)
- ``prices_history``  — long-формат прайс-истории (condition_id, token_id, t, p)

DuckDB читает parquet напрямую — не дублируем данные, просто регистрируем
файлы как view'ы.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
from loguru import logger


DEFAULT_DB_PATH = "data/processed/gadalka.duckdb"
MARKETS_PARQUET_GLOB = "data/raw/markets_*.parquet"
PRICES_PARQUET_GLOB = "data/raw/prices_history/*.parquet"


class GadalkaDB:
    """Лёгкая обёртка над DuckDB.

    Использование::

        with GadalkaDB() as db:
            df = db.sql("SELECT category, COUNT(*) FROM markets GROUP BY 1")
    """

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn: duckdb.DuckDBPyConnection | None = None

    def __enter__(self) -> "GadalkaDB":
        self.conn = duckdb.connect(str(self.db_path))
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def register_parquet_views(self, root: Path | str = ".") -> None:
        """Регистрирует view ``markets`` и ``prices_history`` из parquet-файлов."""
        if self.conn is None:
            raise RuntimeError("DB не открыта — используй with GadalkaDB() as db")

        root = Path(root)
        markets_glob = str(root / MARKETS_PARQUET_GLOB)
        prices_glob = str(root / PRICES_PARQUET_GLOB)

        logger.info("Регистрируем views: markets ← {m}, prices_history ← {p}",
                   m=markets_glob, p=prices_glob)

        # union_by_name=true — устойчиво к разным схемам между файлами
        self.conn.execute(
            f"""
            CREATE OR REPLACE VIEW markets AS
            SELECT * FROM read_parquet('{markets_glob}', union_by_name=true)
            """
        )
        self.conn.execute(
            f"""
            CREATE OR REPLACE VIEW prices_history AS
            SELECT * FROM read_parquet('{prices_glob}', union_by_name=true)
            """
        )
        logger.info("Views зарегистрированы")

    def sql(self, query: str) -> "duckdb.DuckDBPyRelation":
        if self.conn is None:
            raise RuntimeError("DB не открыта")
        return self.conn.sql(query)

    def df(self, query: str):
        """Выполнить SELECT и вернуть pandas DataFrame."""
        if self.conn is None:
            raise RuntimeError("DB не открыта")
        return self.conn.execute(query).df()
