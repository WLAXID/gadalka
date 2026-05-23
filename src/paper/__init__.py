"""Paper trading — Фаза 2.

Не торгует реальными деньгами. Скрепя сердце пишет виртуальные ставки
в DuckDB, ждёт резолва каждого рынка через CLOB, считает форвард-EV.
"""

from src.paper.config import PaperConfig
from src.paper.state import PaperState

__all__ = ["PaperConfig", "PaperState"]
