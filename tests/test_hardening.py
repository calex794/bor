"""Safety checks: authorization, audit persistence, and Docker readiness."""
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from types import SimpleNamespace as NS

from bor.access import can_moderate
from bor.guard_store import GuardStore
from bor.health import check


class PermissionTests(unittest.TestCase):
    def test_owner(self):
        for flow in ("member", "request"):
            self.assertTrue(can_moderate(NS(status="creator"), flow))

    def test_unprivileged_admin_cannot_moderate(self):
        user = NS(status="administrator", can_pin_messages=True,
                  can_restrict_members=False, can_invite_users=False)
        self.assertFalse(can_moderate(user, "member"))
        self.assertFalse(can_moderate(user, "request"))

    def test_exact_capability(self):
        member = NS(status="administrator", can_restrict_members=True, can_invite_users=False)
        self.assertTrue(can_moderate(member, "member"))
        self.assertFalse(can_moderate(member, "request"))
        inviter = NS(status="administrator", can_invite_users=True, can_restrict_members=False)
        self.assertFalse(can_moderate(inviter, "member"))
        self.assertTrue(can_moderate(inviter, "request"))

    def test_unknown_fails_closed(self):
        self.assertFalse(can_moderate(NS(status="member"), "member"))
        self.assertFalse(can_moderate(NS(status="administrator"), "member"))
        self.assertFalse(can_moderate(None, "member"))


class AuditTests(unittest.TestCase):
    def test_records_survive_restart(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "bot.sqlite3"
            with GuardStore(path) as db:
                db.enable(-1001, True, actor_id=19)
                r = db.begin(-1001, 23, "request", 300, 1000, 120, now=1000)
                self.assertTrue(db.stage(r, "sending", "pending", now=1001))
                self.assertTrue(db.reserve_action(r, "approve", now=1002,
                                                  actor_id=19, source="administrator"))
                self.assertTrue(db.finalize(r, "passed", now=1003))
            with GuardStore(path) as db:
                audit = db.audit_history(-1001, 23)
                self.assertEqual(audit[0]["event"], "telegram_confirmed")
                self.assertEqual(audit[1]["actor_id"], 19)
                self.assertEqual(audit[1]["action"], "approve")

    def test_double_reservation_only_once(self):
        with TemporaryDirectory() as tmp:
            with GuardStore(Path(tmp) / "bot.sqlite3") as db:
                r = db.begin(-1001, 23, "request", 300, 1000, 120, now=1000)
                db.stage(r, "sending", "pending")
                self.assertTrue(db.reserve_action(r, "approve", actor_id=20))
                self.assertFalse(db.reserve_action(r, "decline", actor_id=21))
                actions = [x for x in db.audit_history(-1001, 23) if x["event"].startswith("reserved")]
                self.assertEqual(len(actions), 1)


class HealthTests(unittest.TestCase):
    def test_stale_polling_is_unhealthy(self):
        from unittest.mock import patch
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "bot.sqlite3"
            with GuardStore(path):
                pass
            with patch.dict("os.environ", {"BOR_DATABASE_PATH": str(path)}):
                self.assertFalse(check())
                t = str(int(time.time()))
                (Path(tmp) / ".bor-polling").write_text(t)
                (Path(tmp) / ".bor-sweep").write_text(t)
                self.assertTrue(check())
                (Path(tmp) / ".bor-polling").write_text(str(int(time.time())-500))
                self.assertFalse(check())

    def test_health_closes_connection(self):
        from unittest.mock import patch
        import sqlite3
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "bot.sqlite3"
            with GuardStore(path):
                pass
            stamp = str(int(time.time()))
            (Path(tmp) / ".bor-polling").write_text(stamp)
            (Path(tmp) / ".bor-sweep").write_text(stamp)
            real_connect = sqlite3.connect
            seen = []

            class TrackedConnection:
                def __init__(self, wrapped):
                    self.wrapped = wrapped
                    self.closed = False
                def __enter__(self):
                    self.wrapped.__enter__()
                    return self
                def __exit__(self, *exc):
                    return self.wrapped.__exit__(*exc)
                def execute(self, *args, **kwargs):
                    return self.wrapped.execute(*args, **kwargs)
                def close(self):
                    self.closed = True
                    self.wrapped.close()

            def connect(*args, **kwargs):
                instance = TrackedConnection(real_connect(*args, **kwargs))
                seen.append(instance)
                return instance

            with patch.dict("os.environ", {"BOR_DATABASE_PATH": str(path)}):
                with patch("bor.health.sqlite3.connect", side_effect=connect):
                    self.assertTrue(check())
            self.assertEqual(len(seen), 1)
            self.assertTrue(seen[0].closed)
