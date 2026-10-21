"""No BotFather token needed; exercises persisted moderation transitions."""

import tempfile
import unittest
from pathlib import Path

from bor.guard_store import GuardStore


class GuardStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "guard.sqlite3"
        self.store = GuardStore(self.path)
        self.addCleanup(self.store.close)

    def create(self, flow="member", event_time=1200, now=1200, seconds=120):
        record = self.store.begin(-100121, 123456, flow,
                                  777777 if flow == "request" else -100121,
                                  event_time, seconds, now=now)
        self.assertIsNotNone(record)
        return record

    def test_default_is_off(self):
        self.assertFalse(self.store.enabled(-100121))
        self.store.enable(-100121, True)
        self.assertTrue(self.store.enabled(-100121))
        self.store.enable(-100121, False)
        self.assertFalse(self.store.enabled(-100121))

    def test_settings_survive_restart(self):
        self.store.enable(-100121, True)
        with GuardStore(self.path) as other:
            self.assertTrue(other.enabled(-100121))

    def test_random_math_options(self):
        r = self.create()
        self.assertEqual(r.status, "restricting")
        self.assertEqual(r.answer, r.a + r.b)
        self.assertEqual(len(r.options), 9)
        self.assertEqual(len(set(r.options)), 9)
        self.assertIn(r.answer, r.options)
        self.assertEqual(r.expires_at, 1320)

    def test_duplicated_membership_event(self):
        self.create()
        self.assertIsNone(self.store.begin(-100121, 123456, "member", -100121, 1200, 120, now=1201))

    def test_member_delivery(self):
        r = self.create()
        self.assertTrue(self.store.stage(r, "restricting", "sending", now=1201))
        self.assertTrue(self.store.stage(r, "sending", "pending", message_id=42, now=1202))
        self.assertEqual(self.store.get(r.chat_id, r.user_id).message_id, 42)
        self.assertFalse(self.store.stage(r, "sending", "pending", message_id=99))

    def test_wrong_callback_atomic_dedup(self):
        r = self.create()
        self.store.stage(r, "restricting", "sending")
        self.store.stage(r, "sending", "pending")
        wrong = next(n for n in r.options if n != r.answer)
        d1 = self.store.answer(r.chat_id, r.user_id, r.nonce, wrong, "callback1", 3, now=1210)
        self.assertEqual(d1.kind, "wrong")
        d2 = self.store.answer(r.chat_id, r.user_id, r.nonce, wrong, "callback1", 3, now=1211)
        self.assertEqual(d2.kind, "duplicate")
        self.assertEqual(self.store.get(r.chat_id, r.user_id).attempts, 1)

    def test_correct_answer_transitions_once(self):
        r = self.create()
        self.store.stage(r, "restricting", "sending")
        self.store.stage(r, "sending", "pending")
        d = self.store.answer(r.chat_id, r.user_id, r.nonce, r.answer, "callback-ok", 3, now=1210)
        self.assertEqual((d.kind, d.action), ("correct", "unrestrict"))
        self.assertEqual(self.store.get(r.chat_id, r.user_id).status, "acting")
        self.assertEqual(self.store.answer(r.chat_id, r.user_id, r.nonce, r.answer,
                                           "callback-new", 3, now=1211).kind, "stale")
        self.assertTrue(self.store.finalize(r, "passed", now=1212))
        self.assertFalse(self.store.finalize(r, "passed", now=1213))

    def test_requests_approve_and_skip_second_captcha(self):
        r = self.create("request")
        self.store.stage(r, "sending", "pending")
        d = self.store.answer(r.chat_id, r.user_id, r.nonce, r.answer, "request-ok", 3, now=1210)
        self.assertEqual(d.action, "approve")
        self.store.finalize(r, "passed", now=1211)
        self.assertIsNone(self.store.begin(r.chat_id, r.user_id, "member", r.chat_id, 1212,
                                           120, now=1212))
        self.assertIsNotNone(self.store.begin(r.chat_id, r.user_id, "member", r.chat_id, 2300,
                                              120, now=2300))

    def test_attempts_exhausted_trigger_kick(self):
        r = self.create()
        self.store.stage(r, "restricting", "sending")
        self.store.stage(r, "sending", "pending")
        wrong = next(n for n in r.options if n != r.answer)
        for i in range(2):
            self.assertEqual(self.store.answer(r.chat_id,r.user_id,r.nonce,wrong,
                                               f"w{i}",3,now=1210+i).kind,"wrong")
        final = self.store.answer(r.chat_id,r.user_id,r.nonce,wrong,"w3",3,now=1215)
        self.assertEqual((final.kind,final.action), ("failed", "kick"))
        self.assertEqual(self.store.get(r.chat_id, r.user_id).status, "acting")

    def test_timeout_denies_late_answer(self):
        r = self.create("request")
        self.store.stage(r, "sending", "pending")
        d = self.store.answer(r.chat_id,r.user_id,r.nonce,r.answer,"late",3,now=1320)
        self.assertEqual(d.kind,"expired")
        self.assertEqual(len(self.store.due(now=1320)),1)
        self.assertTrue(self.store.reserve_action(r,"decline",now=1321))
        self.assertFalse(self.store.reserve_action(r,"decline",now=1321))
        self.assertTrue(self.store.finalize(r,"denied",now=1322))

    def test_manual_after_unknown_result(self):
        r = self.create()
        self.store.manual(r, "restrict_unknown")
        self.assertEqual(self.store.get(r.chat_id,r.user_id).status,"manual")
        self.assertEqual(len(self.store.pending(r.chat_id)),1)
        self.assertTrue(self.store.reserve_action(r,"unrestrict",now=1300))
        self.assertTrue(self.store.finalize(r,"passed",now=1301))

    def test_recover_unchanged_state_after_restart(self):
        r = self.create()
        self.store.stage(r,"restricting","sending")
        self.store.stage(r,"sending","pending",message_id=55)
        with GuardStore(self.path) as other:
            self.assertEqual(other.get(r.chat_id,r.user_id).nonce,r.nonce)
            self.assertEqual(other.get(r.chat_id,r.user_id).message_id,55)

    def test_uncertain_action_marked_manual(self):
        r = self.create("request")
        self.assertEqual(self.store.orphans()[0].status,"sending")
        self.store.manual(r,"interrupted")
        self.assertFalse(self.store.orphans())

    def test_invalid_options_cannot_increase_attempts(self):
        r = self.create()
        self.store.stage(r,"restricting","sending")
        self.store.stage(r,"sending","pending")
        self.assertEqual(self.store.answer(r.chat_id,r.user_id,r.nonce,-300,
                                           "fake",3,now=1210).kind,"invalid")
        self.assertEqual(self.store.get(r.chat_id,r.user_id).attempts,0)

    def test_stale_nonce_is_not_accepted(self):
        r = self.create("request")
        self.store.stage(r,"sending","pending")
        self.assertEqual(self.store.answer(r.chat_id,r.user_id,"wrong",r.answer,
                                           "fake",3,now=1210).kind,"stale")


if __name__ == "__main__":
    unittest.main()
