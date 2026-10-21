"""Check SQLite writable and fresh successful polling/sweep signals.

getMe alone is NOT sufficient evidence that a bot processes Telegram updates.
"""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import time


def check() -> bool:
    database = Path(os.getenv("BOR_DATABASE_PATH", "/data/bor.sqlite3"))
    root = database.parent
    if not database.is_file():
        return False
    now = time.time()
    for name, max_age in (("polling", 135), ("sweep", 40)):
        try:
            stamp = int((root / f".bor-{name}").read_text(encoding="ascii"))
        except (OSError, ValueError):
            return False
        if not (0 <= now - stamp <= max_age):
            return False
    try:
        # Connection context manager only commits/rolls back; it does not close.
        with closing(sqlite3.connect(database, timeout=3)) as db:
            with db:
                db.execute("PRAGMA busy_timeout=3000")
                db.execute("CREATE TABLE IF NOT EXISTS health_probe(id INTEGER PRIMARY KEY, touched INTEGER)")
                db.execute("INSERT OR REPLACE INTO health_probe(id,touched) VALUES (1, ?)",
                           (int(now),))
        return True
    except sqlite3.Error:
        return False


if __name__ == "__main__":
    raise SystemExit(0 if check() else 1)
