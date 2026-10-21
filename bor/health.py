"""Docker health probe: validate Bot API connectivity without logging secrets."""

import asyncio
import os

from aiogram import Bot


async def check() -> bool:
    token = os.getenv("BOT_TOKEN", "")
    if not token or token == "replace-with-private-token":
        return False
    bot = Bot(token=token)
    try:
        await asyncio.wait_for(bot.get_me(), timeout=8)
        return True
    except Exception:
        return False
    finally:
        await bot.session.close()


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(check()) else 1)
