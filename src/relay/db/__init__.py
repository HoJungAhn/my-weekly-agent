"""SQLite 접근 레이어 — 원본(source of truth).

설계 결정 #1·#2 참조.
"""

from relay.db.schema import SCHEMA_SQL
from relay.db.store import Store, connect, init_db

__all__ = ["SCHEMA_SQL", "Store", "connect", "init_db"]
