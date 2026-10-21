import json
from pathlib import Path
import tempfile
import unittest

from bor.catalog import Book, load_books


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "books.json"

    def write(self, data):
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_load_valid_books(self):
        self.write({"some_book": {"title": "  Имя  ", "description": " Описание ",
                                   "url": "https://example.org/novels/1"}})
        self.assertEqual(load_books(self.path)["some_book"],
                         Book("Имя", "Описание", "https://example.org/novels/1"))

    def test_reject_unsafe_links(self):
        for url in ["http://example.org", "javascript:alert(1)", "https://a@host.invalid/path", "file:///tmp/a"]:
            with self.subTest(url=url):
                self.write({"demo": {"title": "Title", "url": url}})
                with self.assertRaises(ValueError):
                    load_books(self.path)

    def test_reject_bad_objects(self):
        for data in [[], {"../bad": {"title": "Title"}}, {"ok": []},
                     {"ok": {"title": ""}}, {"ok": {"description": "No title"}}]:
            with self.subTest(data=data):
                self.write(data)
                with self.assertRaises(ValueError):
                    load_books(self.path)

    def test_real_demo_catalog(self):
        demo = load_books(Path(__file__).resolve().parents[1] / "catalog.json")
        self.assertIn("demo", demo)
        self.assertFalse(demo["demo"].url)


if __name__ == "__main__":
    unittest.main()
