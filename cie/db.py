"""SQLite connection, transactions and schema setup."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = Path(__file__).with_name("schema.sql")

SYSTEM_TABLES = {"requirements": "requirement", "parts": "part", "tests": "test_case"}
PREFIX_SYSTEM = {"REQ": "requirements", "PRT": "parts", "TST": "tests"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def tx(conn: sqlite3.Connection):
    """One write transaction. BEGIN IMMEDIATE takes the write lock up front, so the
    audit hash chain can't fork when two writers race."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))


def system_of(item_id: str) -> str:
    try:
        return PREFIX_SYSTEM[item_id.split("-", 1)[0]]
    except KeyError:
        raise ValueError(f"unknown item id {item_id!r}") from None


def get_item(conn: sqlite3.Connection, item_id: str):
    return conn.execute("SELECT * FROM item WHERE id = ?", (item_id,)).fetchone()
