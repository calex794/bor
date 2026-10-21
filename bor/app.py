"""First running version of Rebuild: commands, book cards, and deep links."""

import asyncio
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from .catalog import Book, load_books
from .deeplink import route_start, valid_book_id

LOGGER = logging.getLogger("bor")


def create_router(books: dict[str, Book], build_sha: str) -> Router:
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
        # Plain text by design: book titles/descriptions cannot inject HTML/Markdown.
        await message.answer(
            f"{book.title}\n\n{book.description}".strip(),
            reply_markup=buttons,
        )

    @router.message(CommandStart())
    async def start(message: Message, command: CommandObject) -> None:
        route = route_start(command.args)
        if route.kind == "book":
            await send_book(message, route.identifier)
        elif route.kind == "help":
            await message.answer("Команды: /start, /help, /version, /book demo")
        elif route.kind == "invalid":
            await message.answer("Ссылка устарела или содержит неверный параметр.")
        else:
            await message.answer(
                "Бот Ребилд запущен.\n"
                "Команды: /help, /version, /book demo.\n"
                "Адресная ссылка на книгу: ?start=book_demo"
            )

    @router.message(Command("help"))
    async def help_command(message: Message) -> None:
        await message.answer("/start — главное меню\n/help — команды\n/version — версия\n/book demo — пример карточки")

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
        raise RuntimeError("BOT_TOKEN is missing. Set it in the private .env file.")
    books = load_books(Path(os.getenv("BOR_CATALOG_PATH", "catalog.json")))
    build_sha = os.getenv("BOR_BUILD_SHA", "local")
    bot = Bot(token=token)
    try:
        # Don't silently remove a webhook owned by another bot deployment.
        hook = await bot.get_webhook_info()
        if hook.url:
            raise RuntimeError("A Telegram webhook is configured; disable it manually before polling")
        me = await bot.get_me()
        LOGGER.info("Starting @%s, release=%s", me.username, build_sha[:12])
        dp = Dispatcher()
        dp.include_router(create_router(books, build_sha))
        await dp.start_polling(bot, close_bot_session=False)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
