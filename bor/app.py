"""BOR entry point: anti-bot first, optional demo book/navigation commands."""

import asyncio
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from .catalog import Book, load_books
from .deeplink import route_start, valid_book_id
from .guard import Guard
from .runtime import MonitoredBot
from .guard_store import GuardStore
from .instance_lock import exclusive_database

LOGGER = logging.getLogger("bor")


def create_router(books: dict[str, Book], build_sha: str) -> Router:
    """Optional reader commands. These do not run moderation or change chat rights."""
    router = Router()

    async def send_book(message: Message, book_id: str) -> None:
        book = books.get(book_id)
        if book is None:
            await message.answer("Книга не найдена или карточка больше не доступна.")
            return
        buttons = (
            InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Открыть источник", url=book.url)
            ]]) if book.url else None
        )
        await message.answer(
            f"{book.title}\n\n{book.description}".strip(), reply_markup=buttons,
        )

    @router.message(CommandStart())
    async def start(message: Message, command: CommandObject) -> None:
        route = route_start(command.args)
        if route.kind == "book":
            await send_book(message, route.identifier)
        elif route.kind == "help":
            await message.answer("Антибот: /guard on, /guard off, /status, /pending, /allow ID, /deny ID")
        elif route.kind == "invalid":
            await message.answer("Ссылка устарела или содержит неверный параметр.")
        else:
            await message.answer(
                "Бот Ребилд — защита Telegram-группы.\n"
                "Добавьте бота администратором тестовой супергруппы, затем /guard on.\n"
                "Команды администратора: /status, /pending, /allow ID, /deny ID."
            )

    @router.message(Command("help"))
    async def help_command(message: Message) -> None:
        await message.answer(
            "/guard on — включить проверку новых участников в супергруппе\n"
            "/guard off — остановить новые проверки\n"
            "/status — состояние\n/pending — незавершённые проверки\n"
            "/allow ID — пропустить пользователя\n/deny ID — отклонить\n"
            "/version — версия бота"
        )

    @router.message(Command("version"))
    async def version(message: Message) -> None:
        await message.answer(f"Версия бота: {build_sha[:12]}")

    @router.message(Command("book"))
    async def book_command(message: Message, command: CommandObject) -> None:
        identifier = (command.args or "").strip()
        if not valid_book_id(identifier):
            await message.answer("Укажите код книги: /book demo")
            return
        await send_book(message, identifier)

    return router


async def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token or token == "replace-with-private-token":
        raise RuntimeError("BOT_TOKEN is missing. Set it in a private .env file.")
    database = Path(os.getenv("BOR_DATABASE_PATH", "./data/bor.sqlite3"))
    books = load_books(Path(os.getenv("BOR_CATALOG_PATH", "catalog.json")))
    build_sha = os.getenv("BOR_BUILD_SHA", "local")
    pulse_dir = database.parent
    for kind in ("polling", "sweep"):
        (pulse_dir / f".bor-{kind}").unlink(missing_ok=True)
    bot = MonitoredBot(token=token, pulse_dir=pulse_dir)
    try:
        with exclusive_database(database):
            store = GuardStore(database)
            try:
                hook = await bot.get_webhook_info()
                if hook.url:
                    raise RuntimeError("Telegram webhook exists; stop it manually before long polling")
                me = await bot.get_me()
                LOGGER.info("Starting @%s, release=%s", me.username, build_sha[:12])
                guard = Guard(store, me.id,
                              seconds=int(os.getenv("BOR_CAPTCHA_SECONDS", "120")),
                              max_attempts=int(os.getenv("BOR_CAPTCHA_ATTEMPTS", "3")))
                dp = Dispatcher()
                dp.include_router(guard.router())
                dp.include_router(create_router(books, build_sha))
                await guard.sweep(bot)
                sweeper = asyncio.create_task(guard.sweep_forever(bot))
                try:
                    await dp.start_polling(bot, close_bot_session=False, handle_as_tasks=False,
                                           allowed_updates=["message", "callback_query", "chat_member",
                                                            "chat_join_request"])
                finally:
                    sweeper.cancel()
                    await asyncio.gather(sweeper, return_exceptions=True)
            finally:
                store.close()
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
