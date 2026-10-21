"""API contract tests with a mocked Telegram bot, no network or BotFather token."""

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock
import tempfile

from bor.guard import Guard
from bor.guard_store import GuardStore


class GuardIntegrationTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = GuardStore(Path(self.tmp.name) / "state.sqlite3")
        self.addCleanup(self.store.close)
        self.guard = Guard(self.store, bot_id=777, seconds=120, max_attempts=3)
        self.bot = NS(
            restrict_chat_member=AsyncMock(return_value=True),
            send_message=AsyncMock(return_value=NS(message_id=10)),
            get_chat_member=AsyncMock(return_value=NS(status="restricted", is_member=True)),
            get_chat=AsyncMock(return_value=NS(permissions=NS(can_send_messages=True))),
            approve_chat_join_request=AsyncMock(return_value=True),
            decline_chat_join_request=AsyncMock(return_value=True),
            ban_chat_member=AsyncMock(return_value=True),
        )
        self.now = int(datetime.now(tz=timezone.utc).timestamp())
        self.chat = NS(id=-1001001, type="supergroup")

    def member_event(self, is_admin=False):
        user = NS(id=12345, is_bot=False)
        return NS(chat=self.chat, date=datetime.fromtimestamp(self.now, tz=timezone.utc),
                  old_chat_member=NS(status="left", user=user),
                  new_chat_member=NS(status="administrator" if is_admin else "member", user=user))

    async def test_disabled_by_default(self):
        await self.guard.on_member(self.member_event(), self.bot)
        self.bot.restrict_chat_member.assert_not_called()
        self.assertEqual(self.store.pending(self.chat.id), [])

    async def test_new_member_is_restricted_before_captcha(self):
        self.store.enable(self.chat.id, True)
        await self.guard.on_member(self.member_event(), self.bot)
        record = self.store.get(self.chat.id, 12345)
        self.assertEqual(record.status, "pending")
        self.bot.restrict_chat_member.assert_awaited_once()
        self.bot.send_message.assert_awaited_once()
        self.assertEqual(self.bot.send_message.await_args.kwargs["chat_id"], self.chat.id)

    async def test_duplicate_member_event_sends_only_once(self):
        self.store.enable(self.chat.id, True)
        event = self.member_event()
        await self.guard.on_member(event, self.bot)
        await self.guard.on_member(event, self.bot)
        self.bot.restrict_chat_member.assert_awaited_once()
        self.bot.send_message.assert_awaited_once()

    async def test_never_restrict_admins(self):
        self.store.enable(self.chat.id, True)
        await self.guard.on_member(self.member_event(is_admin=True), self.bot)
        self.bot.restrict_chat_member.assert_not_called()

    async def test_request_sent_to_temporary_private_chat(self):
        self.store.enable(self.chat.id, True)
        event = NS(chat=self.chat, from_user=NS(id=12345, is_bot=False),
                   date=datetime.fromtimestamp(self.now, tz=timezone.utc), user_chat_id=999888)
        await self.guard.on_request(event, self.bot)
        r = self.store.get(self.chat.id, 12345)
        self.assertEqual((r.flow, r.delivery_chat_id, r.status), ("request", 999888, "pending"))
        self.bot.restrict_chat_member.assert_not_called()

    async def test_correct_callback_unrestricts_and_deduplicates(self):
        self.store.enable(self.chat.id, True)
        await self.guard.on_member(self.member_event(), self.bot)
        r = self.store.get(self.chat.id, 12345)
        query = NS(data=f"g1:{r.chat_id}:{r.nonce}:{r.answer}", id="cb1",
                   from_user=NS(id=r.user_id), message=NS(chat=NS(id=self.chat.id)),
                   answer=AsyncMock())
        await self.guard.on_callback(query, self.bot)
        self.assertEqual(self.store.get(r.chat_id, r.user_id).status, "passed")
        self.assertEqual(self.bot.restrict_chat_member.await_count, 2)
        await self.guard.on_callback(query, self.bot)
        self.assertEqual(self.bot.restrict_chat_member.await_count, 2)

    async def test_timeout_does_not_kick_manually_released_user(self):
        self.store.enable(self.chat.id, True)
        await self.guard.on_member(self.member_event(), self.bot)
        r = self.store.get(self.chat.id, 12345)
        self.bot.get_chat_member.return_value = NS(status="member")
        await self.guard.expire(r, self.bot)
        self.assertEqual(self.store.get(r.chat_id,r.user_id).status, "passed")
        self.bot.ban_chat_member.assert_not_called()

    async def test_explicit_admin_deny_can_kick_member(self):
        self.store.enable(self.chat.id, True)
        await self.guard.on_member(self.member_event(), self.bot)
        r = self.store.get(self.chat.id, 12345)
        self.bot.get_chat_member.return_value = NS(status="member")
        self.assertTrue(self.store.reserve_action(r, "kick"))
        await self.guard.execute(r, "kick", self.bot, explicitly_denied=True)
        self.bot.ban_chat_member.assert_awaited_once()
        self.assertEqual(self.store.get(r.chat_id, r.user_id).status, "denied")

    async def test_join_request_correct_approval(self):
        self.store.enable(self.chat.id, True)
        event = NS(chat=self.chat, from_user=NS(id=12345, is_bot=False),
                   date=datetime.fromtimestamp(self.now, tz=timezone.utc), user_chat_id=999888)
        await self.guard.on_request(event, self.bot)
        r = self.store.get(self.chat.id, 12345)
        self.bot.get_chat_member.return_value = NS(status="left")
        callback = NS(data=f"g1:{r.chat_id}:{r.nonce}:{r.answer}", id="join-success",
                      from_user=NS(id=r.user_id), message=NS(chat=NS(id=999888)),
                      answer=AsyncMock())
        await self.guard.on_callback(callback, self.bot)
        self.bot.approve_chat_join_request.assert_awaited_once()
        self.assertEqual(self.store.get(r.chat_id, r.user_id).status, "passed")

    async def test_join_request_timeout_declines(self):
        self.store.enable(self.chat.id, True)
        event = NS(chat=self.chat, from_user=NS(id=12345, is_bot=False),
                   date=datetime.fromtimestamp(self.now, tz=timezone.utc), user_chat_id=999888)
        await self.guard.on_request(event, self.bot)
        r = self.store.get(self.chat.id, 12345)
        self.bot.get_chat_member.return_value = NS(status="left")
        await self.guard.expire(r, self.bot)
        self.bot.decline_chat_join_request.assert_awaited_once()
        self.assertEqual(self.store.get(r.chat_id, r.user_id).status, "denied")

    async def test_uncertain_restrict_does_not_issue_captcha(self):
        self.store.enable(self.chat.id, True)
        self.bot.restrict_chat_member.side_effect = TimeoutError("unknown outcome")
        await self.guard.on_member(self.member_event(), self.bot)
        self.assertEqual(self.store.get(self.chat.id, 12345).status, "manual")
        self.bot.send_message.assert_not_called()

    async def test_wrong_user_cannot_pass_another_challenge(self):
        self.store.enable(self.chat.id, True)
        await self.guard.on_member(self.member_event(), self.bot)
        r = self.store.get(self.chat.id, 12345)
        attacker = NS(data=f"g1:{r.chat_id}:{r.nonce}:{r.answer}", id="cb-other",
                      from_user=NS(id=54321), message=NS(chat=NS(id=self.chat.id)),
                      answer=AsyncMock())
        await self.guard.on_callback(attacker, self.bot)
        self.assertEqual(self.store.get(self.chat.id,12345).status,"pending")
        self.assertEqual(self.bot.restrict_chat_member.await_count, 1)
