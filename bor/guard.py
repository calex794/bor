"""Telegram group entry screening: verify newcomers before granting chat access.

Group settings are opt-in. All moderation mutations are recorded as 'acting'
*before* a Telegram API call, so an uncertain response is never replayed blindly.
"""

import asyncio
from datetime import datetime
import logging
import time

from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery, ChatJoinRequest, ChatMemberUpdated, ChatPermissions,
    InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

from .guard_store import Challenge, GuardStore
from .runtime import beat
from .access import can_moderate

LOG = logging.getLogger("bor.guard")


def _present(member: object) -> bool:
    status = getattr(member, "status", None)
    if status in ("creator", "administrator", "member"):
        return True
    return status == "restricted" and bool(getattr(member, "is_member", False))


def _admin(member: object) -> bool:
    return getattr(member, "status", None) in ("creator", "administrator")


def _event_time(value: datetime | int) -> int:
    return int(value.timestamp()) if isinstance(value, datetime) else int(value)


def _buttons(record: Challenge) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=str(n), callback_data=f"g1:{record.chat_id}:{record.nonce}:{n}")
         for n in record.options[i:i + 3]]
        for i in range(0, 9, 3)
    ])


class Guard:
    def __init__(self, store: GuardStore, bot_id: int,
                 seconds: int = 120, max_attempts: int = 3):
        if not 30 <= seconds <= 900 or not 1 <= max_attempts <= 9:
            raise ValueError("CAPTCHA seconds must be 30..900, attempts 1..9")
        self.store = store
        self.bot_id = bot_id
        self.seconds = seconds
        self.max_attempts = max_attempts
        self.lock = asyncio.Lock()

    def router(self) -> Router:
        router = Router(name="bor-antibot")

        @router.chat_member()
        async def member_joined(event: ChatMemberUpdated, bot: Bot) -> None:
            async with self.lock:
                await self.on_member(event, bot)

        @router.chat_join_request()
        async def join_requested(event: ChatJoinRequest, bot: Bot) -> None:
            async with self.lock:
                await self.on_request(event, bot)

        @router.callback_query(lambda q: bool(q.data and q.data.startswith("g1:")))
        async def captcha_answered(query: CallbackQuery, bot: Bot) -> None:
            async with self.lock:
                await self.on_callback(query, bot)

        @router.message(Command("guard"))
        async def guard_command(message: Message, command: CommandObject, bot: Bot) -> None:
            async with self.lock:
                await self.on_guard(message, command, bot)

        @router.message(Command("status"))
        async def status_command(message: Message, bot: Bot) -> None:
            async with self.lock:
                await self.on_status(message, bot)

        @router.message(Command("pending"))
        async def pending_command(message: Message, bot: Bot) -> None:
            async with self.lock:
                await self.on_pending(message, bot)

        @router.message(Command("allow"))
        async def allow_command(message: Message, command: CommandObject, bot: Bot) -> None:
            async with self.lock:
                await self.on_override(message, command, bot, allow=True)

        @router.message(Command("deny"))
        async def deny_command(message: Message, command: CommandObject, bot: Bot) -> None:
            async with self.lock:
                await self.on_override(message, command, bot, allow=False)

        @router.message(Command("audit"))
        async def audit_command(message: Message, command: CommandObject, bot: Bot) -> None:
            async with self.lock:
                await self.on_audit(message, command, bot)

        return router

    async def on_member(self, event: ChatMemberUpdated, bot: Bot) -> None:
        if event.chat.type != "supergroup" or not self.store.enabled(event.chat.id):
            return
        new, old = event.new_chat_member, event.old_chat_member
        if _present(old) or not _present(new) or _admin(new):
            return
        user = new.user
        if user.id == self.bot_id or user.is_bot:
            # Never auto-restrict administrators, other service bots, or ourselves.
            return
        joined = _event_time(event.date)
        now = int(time.time())
        if now - joined > 600:
            LOG.warning("Skipping old membership event for group %s", event.chat.id)
            return
        record = self.store.begin(event.chat.id, user.id, "member", event.chat.id,
                                  joined, self.seconds, now=now)
        if record is None:
            return
        try:
            ok = await bot.restrict_chat_member(
                chat_id=record.chat_id, user_id=record.user_id,
                permissions=ChatPermissions(can_send_messages=False),
                use_independent_chat_permissions=True,
            )
            if not ok:
                raise RuntimeError("Telegram did not confirm restriction")
        except Exception as error:
            LOG.warning("Failed to restrict a newcomer in %s: %s", record.chat_id, type(error).__name__)
            self.store.manual(record, "restrict_unknown")
            return
        if self.store.stage(record, "restricting", "sending"):
            await self.deliver(record, bot)

    async def on_request(self, event: ChatJoinRequest, bot: Bot) -> None:
        if event.chat.type != "supergroup" or not self.store.enabled(event.chat.id):
            return
        user = event.from_user
        if user.is_bot or user.id == self.bot_id:
            return
        joined = _event_time(event.date)
        now = int(time.time())
        if now - joined > 300:
            return
        record = self.store.begin(event.chat.id, user.id, "request",
                                  int(event.user_chat_id), joined, self.seconds, now=now)
        if record:
            await self.deliver(record, bot)

    async def deliver(self, record: Challenge, bot: Bot) -> None:
        if self.store.get(record.chat_id, record.user_id).status != "sending":
            return
        text = (f"Проверка при вступлении: сколько будет {record.a} + {record.b}?\n"
                f"Время: {self.seconds} сек. Попыток: {self.max_attempts}.\n"
                "Нажмите правильный ответ ниже.")
        try:
            message = await bot.send_message(
                chat_id=record.delivery_chat_id, text=text,
                reply_markup=_buttons(record),
            )
        except Exception as error:
            LOG.warning("Cannot deliver CAPTCHA for group %s: %s", record.chat_id, type(error).__name__)
            self.store.manual(record, "delivery_unknown")
            return
        self.store.stage(record, "sending", "pending", message_id=message.message_id)

    async def on_callback(self, query: CallbackQuery, bot: Bot) -> None:
        if not query.data or query.from_user is None:
            return
        parts = query.data.split(":")
        if len(parts) != 4 or parts[0] != "g1":
            await query.answer("Неверная кнопка", show_alert=True)
            return
        try:
            chat_id, nonce, value = int(parts[1]), parts[2], int(parts[3])
        except ValueError:
            await query.answer("Неверная кнопка", show_alert=True)
            return
        record = self.store.get(chat_id, query.from_user.id)
        if record and getattr(getattr(query, "message", None), "chat", None):
            if query.message.chat.id != record.delivery_chat_id:
                await query.answer("Чужая проверка", show_alert=True)
                return
        decision = self.store.answer(chat_id, query.from_user.id, nonce, value,
                                     query.id, self.max_attempts)
        if decision.kind == "expired" and decision.challenge:
            await self.expire(decision.challenge, bot)
        elif decision.action and decision.challenge:
            await self.execute(decision.challenge, decision.action, bot)
        captions = {
            "duplicate": "Этот ответ уже учтён", "stale": "Проверка уже закончилась",
            "expired": "Время проверки истекло", "invalid": "Неверная кнопка",
            "wrong": "Неверно, попробуйте ещё", "failed": "Попытки закончились",
            "correct": "Ответ принят — проверяем результат",
        }
        latest = self.store.get(chat_id, query.from_user.id)
        caption = captions.get(decision.kind, "Проверка недоступна")
        if decision.kind == "correct" and latest and latest.status == "passed":
            caption = "Проверка пройдена"
        elif decision.kind in ("correct", "failed") and latest and latest.status == "manual":
            caption = "Нужна проверка администратором"
        await query.answer(caption, show_alert=decision.kind in ("invalid", "stale"))

    async def execute(self, record: Challenge, action: str, bot: Bot,
                      *, explicitly_denied: bool = False) -> None:
        """Perform a reserved action; only an explicit admin denial can kick released members."""
        if self.store.get(record.chat_id, record.user_id).status != "acting":
            return
        try:
            try:
                current = await bot.get_chat_member(record.chat_id, record.user_id)
            except TelegramBadRequest as err:
                # Pending join requests may have no ChatMember entry yet.
                if record.flow == "request" and "user not found" in str(err).lower():
                    current = None
                else:
                    raise
            if _admin(current):
                self.store.finalize(record, "passed")
                return
            if action in ("unrestrict", "kick") and not _present(current):
                self.store.finalize(record, "closed")
                return
            if action in ("unrestrict", "kick") and getattr(current, "status", None) == "member" and not (action == "kick" and explicitly_denied):
                # Someone with admin rights has already released this user.
                # Do not kick them after a timeout or reapply restrictions.
                self.store.finalize(record, "passed")
                return
            if action == "unrestrict":
                chat = await bot.get_chat(record.chat_id)
                if chat.permissions is None:
                    raise RuntimeError("Cannot safely restore chat permissions")
                ok = await bot.restrict_chat_member(
                    chat_id=record.chat_id, user_id=record.user_id,
                    permissions=chat.permissions,
                    use_independent_chat_permissions=True,
                )
                outcome = "passed"
            elif action == "kick":
                ok = await bot.ban_chat_member(
                    chat_id=record.chat_id, user_id=record.user_id,
                    until_date=int(time.time()) + 300,
                    revoke_messages=False,
                )
                outcome = "denied"
            elif action == "approve":
                if _present(current):
                    self.store.finalize(record, "passed")
                    return
                ok = await bot.approve_chat_join_request(
                    chat_id=record.chat_id, user_id=record.user_id,
                )
                outcome = "passed"
            elif action == "decline":
                if _present(current):
                    self.store.finalize(record, "passed")
                    return
                ok = await bot.decline_chat_join_request(
                    chat_id=record.chat_id, user_id=record.user_id,
                )
                outcome = "denied"
            else:
                raise ValueError("Unknown moderation action")
            if not ok:
                raise RuntimeError("Telegram did not confirm moderation")
            self.store.finalize(record, outcome)
        except Exception as error:
            LOG.warning("Moderation action requires manual review in group %s: %s",
                        record.chat_id, type(error).__name__)
            self.store.manual(record, f"{action}_unknown")

    async def expire(self, record: Challenge, bot: Bot) -> None:
        current = self.store.get(record.chat_id, record.user_id)
        if not current or current.nonce != record.nonce or current.status != "pending":
            return
        action = "kick" if current.flow == "member" else "decline"
        if self.store.reserve_action(current, action):
            await self.execute(current, action, bot)

    async def sweep(self, bot: Bot) -> None:
        async with self.lock:
            for record in self.store.orphans():
                # Startup recovery: uncertain external action cannot be blindly retried.
                self.store.manual(record, "interrupted")
            for record in self.store.due():
                await self.expire(record, bot)
            self.store.cleanup(int(time.time()) - 7 * 86400)
            beat(self.store.db.execute("PRAGMA database_list").fetchone()["file"].rsplit("/", 1)[0], "sweep")

    async def sweep_forever(self, bot: Bot) -> None:
        while True:
            try:
                await self.sweep(bot)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOG.exception("Anti-bot maintenance failed")
            await asyncio.sleep(5)

    async def is_admin(self, message: Message, bot: Bot) -> bool:
        if message.chat.type != "supergroup" or message.from_user is None:
            await message.answer("Команда доступна администратору супергруппы.")
            return False
        try:
            member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        except Exception:
            await message.answer("Не удалось проверить права администратора.")
            return False
        if not _admin(member):
            await message.answer("Команда доступна только администратору.")
            return False
        return True

    async def may_moderate(self, message: Message, bot: Bot, flow: str,
                           *, check_bot: bool = True) -> bool:
        if not await self.is_admin(message, bot):
            return False
        try:
            actor = await bot.get_chat_member(message.chat.id, message.from_user.id)
            if not can_moderate(actor, flow):
                await message.answer("Недостаточно ваших прав для этого действия.")
                return False
            if check_bot:
                current_bot = await bot.get_chat_member(message.chat.id, self.bot_id)
                if not can_moderate(current_bot, flow):
                    await message.answer("Боту не выданы нужные права в этой группе.")
                    return False
            return True
        except Exception:
            await message.answer("Не удалось проверить права. Действие отменено.")
            return False

    async def on_guard(self, message: Message, command: CommandObject, bot: Bot) -> None:
        mode = (command.args or "").strip().lower()
        if mode not in ("on", "off"):
            if await self.is_admin(message, bot):
                await message.answer("Используйте: /guard on или /guard off")
            return
        if not await self.may_moderate(message, bot, "member", check_bot=mode == "on"):
            return
        self.store.enable(message.chat.id, mode == "on", actor_id=message.from_user.id)
        await message.answer("Антибот включён." if mode == "on" else
                             "Антибот отключён для новых вступлений. Текущие проверки остаются.")

    async def on_status(self, message: Message, bot: Bot) -> None:
        if not await self.is_admin(message, bot):
            return
        state = "включён" if self.store.enabled(message.chat.id) else "выключен"
        await message.answer(f"Антибот: {state}. Ожидают проверки: {len(self.store.pending(message.chat.id))}.\n"
                             f"Время: {self.seconds} сек. Попыток: {self.max_attempts}.")

    async def on_pending(self, message: Message, bot: Bot) -> None:
        if not await self.is_admin(message, bot):
            return
        pending = self.store.pending(message.chat.id)
        if not pending:
            await message.answer("Незавершённых проверок нет.")
            return
        lines = ["Ожидают решения:"]
        for item in pending:
            lines.append(f"{item.user_id}: {item.flow} / {item.status}")
        lines.append("Команды: /allow ID или /deny ID")
        await message.answer("\n".join(lines))

    async def on_override(self, message: Message, command: CommandObject,
                          bot: Bot, *, allow: bool) -> None:
        if not await self.is_admin(message, bot):
            return
        raw = (command.args or "").strip()
        if not raw.isdecimal():
            await message.answer("Укажите числовой идентификатор: /allow ID или /deny ID")
            return
        record = self.store.get(message.chat.id, int(raw))
        if not record or record.status not in ("pending", "manual"):
            await message.answer("Нет проверки с таким ID, доступной для ручного решения.")
            return
        if not await self.may_moderate(message, bot, record.flow):
            return
        action = (("unrestrict" if allow else "kick") if record.flow == "member"
                  else ("approve" if allow else "decline"))
        if self.store.reserve_action(record, action, actor_id=message.from_user.id,
                                     source="administrator"):
            await self.execute(record, action, bot, explicitly_denied=not allow)
        final = self.store.get(record.chat_id, record.user_id)
        if final and final.status == "manual":
            await message.answer("Telegram не подтвердил действие, нужна ручная проверка.")
        else:
            await message.answer(f"Результат: {final.status if final else 'неизвестно'}")

    async def on_audit(self, message: Message, command: CommandObject, bot: Bot) -> None:
        if not await self.is_admin(message, bot):
            return
        raw = (command.args or "").strip()
        if not raw.isdecimal():
            await message.answer("Укажите ID участника: /audit ID")
            return
        entries = self.store.audit_history(message.chat.id, int(raw), limit=10)
        if not entries:
            await message.answer("Событий для этого участника не найдено.")
            return
        from datetime import datetime, timezone
        lines = ["Последние решения (UTC):"]
        for entry in entries:
            stamp = datetime.fromtimestamp(entry["created_at"], timezone.utc).strftime("%m-%d %H:%M")
            actor = entry["actor_id"] if entry["actor_id"] is not None else "система"
            lines.append(f'{stamp} | {entry["event"]} | {entry["action"] or "-"} | {actor}')
        await message.answer("\n".join(lines))
