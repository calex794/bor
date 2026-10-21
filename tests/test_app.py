"""Offline smoke test for aiogram wiring: no Telegram token or network required."""

from pathlib import Path
import unittest

from bor.app import create_router
from bor.catalog import load_books


class BotAppTests(unittest.TestCase):
    def test_registers_first_user_commands(self):
        books = load_books(Path(__file__).resolve().parents[1] / "catalog.json")
        router = create_router(books, "offline-build")
        self.assertEqual(len(router.message.handlers), 4)


if __name__ == "__main__":
    unittest.main()
