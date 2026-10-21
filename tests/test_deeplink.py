import unittest

from bor.deeplink import Route, route_start, valid_book_id


class DeepLinkTests(unittest.TestCase):
    def test_empty_payload(self):
        self.assertEqual(route_start(None), Route("home"))
        self.assertEqual(route_start(""), Route("home"))

    def test_book_id(self):
        self.assertEqual(route_start("book_demo"), Route("book", "demo"))
        self.assertEqual(route_start("book_A-123"), Route("book", "A-123"))

    def test_help(self):
        self.assertEqual(route_start("help"), Route("help"))

    def test_reject_invalid_payloads(self):
        invalid = ["book_", "book_abc/def", "book_../../secret", "unknown", "привет", "a" * 65,
                   "book_" + "b" * 49, "book_x%3Dy", "book_x y", "book_🙂"]
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assertEqual(route_start(payload), Route("invalid"))

    def test_book_identifiers(self):
        self.assertTrue(valid_book_id("AZ-09_s"))
        self.assertFalse(valid_book_id(""))
        self.assertFalse(valid_book_id("/etc/passwd"))
        self.assertFalse(valid_book_id("книга"))
        self.assertFalse(valid_book_id("a" * 49))


if __name__ == "__main__":
    unittest.main()
