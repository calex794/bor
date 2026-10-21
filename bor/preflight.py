"""Read-only check of BotFather token and rights in a test supergroup."""

import asyncio
import os

from aiogram import Bot


async def main() -> None:
    token = os.getenv("BOT_TOKEN", "").strip()
    raw_id = os.getenv("BOR_TEST_CHAT_ID", "")
    if not token or not raw_id.lstrip("-").isdigit():
        raise SystemExit("Set private BOT_TOKEN and numeric BOR_TEST_CHAT_ID")
    bot = Bot(token=token)
    try:
        me = await bot.get_me()
        hook = await bot.get_webhook_info()
        chat = await bot.get_chat(int(raw_id))
        member = await bot.get_chat_member(int(raw_id), me.id)
        errors = []
        if hook.url:
            errors.append("Webhook already configured, conflicts with polling")
        if chat.type != "supergroup":
            errors.append("Test chat is not a supergroup")
        if member.status not in ("administrator", "creator"):
            errors.append("Bot is not a group admin")
        if not getattr(member, "can_restrict_members", False):
            errors.append("Missing right to restrict members")
        if not getattr(member, "can_invite_users", False):
            errors.append("Missing right to approve join requests")
        if not getattr(member, "can_delete_messages", False):
            print("NOTE: No delete-messages right; first anti-bot version does not need it")
        print(f"Bot: @{me.username or 'no-username'}, chat type: {chat.type}")
        if errors:
            for error in errors:
                print(f"CHECK: {error}")
            raise SystemExit(2)
        print("READY: group and BotFather rights confirmed")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
