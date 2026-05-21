import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

from config import get_config


class DatabaseConnection:

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = str(db_path or get_config().db_path)
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: agents dispatch parallel specialists via
        # ThreadPoolExecutor; SQLite in WAL mode handles concurrent reads
        # safely. Writes are serialized through `transaction()` on the main
        # thread so this is safe.
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.row_factory = sqlite3.Row
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def db_path(self) -> str:
        return self._db_path

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.connect()
        return self._conn

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Cursor, None, None]:
        cursor = self.conn.cursor()
        try:
            yield cursor
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def __enter__(self) -> "DatabaseConnection":
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.close()
