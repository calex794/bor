"""Persistent, single-process CAPTCHA state with idempotent callback handling.

No Telegram calls here: an external action is reserved before it is attempted,
and an uncertain outcome is never automatically repeated.
"""

from dataclasses import dataclass
import json
from pathlib import Path
import secrets
import sqlite3
import time


@dataclass(frozen=True)
class Challenge:
    chat_id: int
    user_id: int
    flow: str
    delivery_chat_id: int
    nonce: str
    a: int
    b: int
    answer: int
    options: tuple[int, ...]
    attempts: int
    expires_at: int
    event_time: int
    status: str
    action: str | None
    message_id: int | None


@dataclass(frozen=True)
class Decision:
    kind: str
    challenge: Challenge | None = None
    action: str | None = None


class GuardStore:
    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS guard_settings (
                chat_id INTEGER PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS challenges (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                flow TEXT NOT NULL,
                delivery_chat_id INTEGER NOT NULL,
                nonce TEXT NOT NULL,
                a INTEGER NOT NULL,
                b INTEGER NOT NULL,
                answer INTEGER NOT NULL,
                options TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                expires_at INTEGER NOT NULL,
                event_time INTEGER NOT NULL,
                status TEXT NOT NULL,
                action TEXT,
                message_id INTEGER,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (chat_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS processed_callbacks (
                callback_id TEXT PRIMARY KEY,
                created_at INTEGER NOT NULL
            );
        """)

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "GuardStore":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def enabled(self, chat_id: int) -> bool:
        row = self.db.execute("SELECT enabled FROM guard_settings WHERE chat_id=?", (chat_id,)).fetchone()
        return bool(row and row["enabled"])

    def enable(self, chat_id: int, enabled: bool) -> None:
        self.db.execute("INSERT INTO guard_settings(chat_id,enabled) VALUES(?,?) "
                        "ON CONFLICT(chat_id) DO UPDATE SET enabled=excluded.enabled",
                        (chat_id, int(enabled)))

    @staticmethod
    def _from_row(row: sqlite3.Row | None) -> Challenge | None:
        if row is None:
            return None
        return Challenge(
            chat_id=row["chat_id"], user_id=row["user_id"], flow=row["flow"],
            delivery_chat_id=row["delivery_chat_id"], nonce=row["nonce"],
            a=row["a"], b=row["b"], answer=row["answer"],
            options=tuple(json.loads(row["options"])), attempts=row["attempts"],
            expires_at=row["expires_at"], event_time=row["event_time"],
            status=row["status"], action=row["action"], message_id=row["message_id"],
        )

    def get(self, chat_id: int, user_id: int) -> Challenge | None:
        return self._from_row(self.db.execute(
            "SELECT * FROM challenges WHERE chat_id=? AND user_id=?", (chat_id, user_id)
        ).fetchone())

    def begin(self, chat_id: int, user_id: int, flow: str, delivery_chat_id: int,
              event_time: int, ttl: int, now: int | None = None) -> Challenge | None:
        """Return a new challenge, or None for a duplicate/recent approved request."""
        if flow not in ("member", "request") or ttl < 30:
            raise ValueError("Invalid flow/ttl")
        now = int(time.time()) if now is None else now
        previous = self.get(chat_id, user_id)
        if previous:
            if previous.status in ("restricting", "sending", "pending", "acting", "manual"):
                return None
            if previous.flow == flow and previous.event_time == event_time:
                return None
            if previous.flow == "request" and previous.status == "passed" \
                    and flow == "member" and 0 <= event_time - previous.event_time < 300:
                return None
        rng = secrets.SystemRandom()
        a, b = rng.randint(2, 9), rng.randint(2, 9)
        answer = a + b
        distractors = rng.sample([n for n in range(2, 21) if n != answer], 8)
        options = distractors + [answer]
        rng.shuffle(options)
        nonce = secrets.token_hex(6)
        stage = "restricting" if flow == "member" else "sending"
        self.db.execute("""
            INSERT INTO challenges
            (chat_id,user_id,flow,delivery_chat_id,nonce,a,b,answer,options,attempts,
            expires_at,event_time,status,action,message_id,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,0,?,?,?,NULL,NULL,?)
            ON CONFLICT(chat_id,user_id) DO UPDATE SET
              flow=excluded.flow,delivery_chat_id=excluded.delivery_chat_id,
              nonce=excluded.nonce,a=excluded.a,b=excluded.b,answer=excluded.answer,
              options=excluded.options,attempts=0,expires_at=excluded.expires_at,
              event_time=excluded.event_time,status=excluded.status,
              action=NULL,message_id=NULL,updated_at=excluded.updated_at
        """, (chat_id,user_id,flow,delivery_chat_id,nonce,a,b,answer,
              json.dumps(options),now+ttl,event_time,stage,now))
        return self.get(chat_id, user_id)

    def stage(self, challenge: Challenge, expected: str, status: str,
              message_id: int | None = None, now: int | None = None) -> bool:
        now = int(time.time()) if now is None else now
        cur = self.db.execute("""
            UPDATE challenges SET status=?,message_id=COALESCE(?,message_id),updated_at=?
            WHERE chat_id=? AND user_id=? AND nonce=? AND status=?
        """, (status,message_id,now,challenge.chat_id,challenge.user_id,challenge.nonce,expected))
        return cur.rowcount == 1

    def manual(self, challenge: Challenge, reason: str = "unknown", now: int | None = None) -> None:
        now = int(time.time()) if now is None else now
        self.db.execute("""
            UPDATE challenges SET status='manual',action=?,updated_at=?
            WHERE chat_id=? AND user_id=? AND nonce=?
        """, (reason[:120],now,challenge.chat_id,challenge.user_id,challenge.nonce))

    def finalize(self, challenge: Challenge, outcome: str, now: int | None = None) -> bool:
        if outcome not in ("passed", "denied", "closed"):
            raise ValueError("Invalid final outcome")
        now = int(time.time()) if now is None else now
        cur = self.db.execute("""
            UPDATE challenges SET status=?,action=NULL,updated_at=?
            WHERE chat_id=? AND user_id=? AND nonce=? AND status='acting'
        """, (outcome,now,challenge.chat_id,challenge.user_id,challenge.nonce))
        return cur.rowcount == 1

    def reserve_action(self, challenge: Challenge, action: str, now: int | None = None) -> bool:
        if action not in ("unrestrict", "approve", "kick", "decline"):
            raise ValueError("Invalid action")
        now = int(time.time()) if now is None else now
        cur = self.db.execute("""
            UPDATE challenges SET status='acting',action=?,updated_at=?
            WHERE chat_id=? AND user_id=? AND nonce=? AND status IN ('pending','manual')
        """, (action,now,challenge.chat_id,challenge.user_id,challenge.nonce))
        return cur.rowcount == 1

    def answer(self, chat_id: int, user_id: int, nonce: str, value: int,
               callback_id: str, attempt_limit: int, now: int | None = None) -> Decision:
        if attempt_limit < 1:
            raise ValueError("Invalid attempt limit")
        now = int(time.time()) if now is None else now
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if self.db.execute("SELECT 1 FROM processed_callbacks WHERE callback_id=?", (callback_id,)).fetchone():
                self.db.execute("COMMIT")
                return Decision("duplicate")
            record = self.get(chat_id,user_id)
            if not record or record.nonce != nonce or record.status != "pending":
                self.db.execute("COMMIT")
                return Decision("stale")
            if record.expires_at <= now:
                self.db.execute("COMMIT")
                return Decision("expired",record)
            if value not in record.options:
                self.db.execute("COMMIT")
                return Decision("invalid",record)
            self.db.execute("INSERT INTO processed_callbacks(callback_id,created_at) VALUES (?,?)", (callback_id,now))
            if value == record.answer:
                action = "unrestrict" if record.flow == "member" else "approve"
                status = "acting"
                kind = "correct"
            else:
                exhausted = record.attempts + 1 >= attempt_limit
                action = ("kick" if record.flow == "member" else "decline") if exhausted else None
                status = "acting" if exhausted else "pending"
                kind = "failed" if exhausted else "wrong"
            self.db.execute("""
                UPDATE challenges SET attempts=attempts+?,status=?,action=?,updated_at=?
                WHERE chat_id=? AND user_id=? AND nonce=?
            """, (int(value != record.answer),status,action,now,chat_id,user_id,nonce))
            new = self.get(chat_id,user_id)
            self.db.execute("COMMIT")
            return Decision(kind,new,action)
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def due(self, now: int | None = None) -> list[Challenge]:
        now = int(time.time()) if now is None else now
        rows = self.db.execute("""
            SELECT * FROM challenges WHERE status='pending' AND expires_at<=?
            ORDER BY expires_at LIMIT 50
        """, (now,)).fetchall()
        return [self._from_row(row) for row in rows]

    def orphans(self) -> list[Challenge]:
        rows = self.db.execute("""
            SELECT * FROM challenges WHERE status IN ('restricting','sending','acting')
            ORDER BY updated_at LIMIT 50
        """).fetchall()
        return [self._from_row(row) for row in rows]

    def pending(self, chat_id: int) -> list[Challenge]:
        rows = self.db.execute("""
            SELECT * FROM challenges WHERE chat_id=? AND status IN
              ('pending','manual','restricting','sending','acting')
            ORDER BY updated_at LIMIT 20
        """, (chat_id,)).fetchall()
        return [self._from_row(row) for row in rows]

    def cleanup(self, before: int) -> None:
        self.db.execute("DELETE FROM processed_callbacks WHERE created_at < ?", (before,))
