"""Tiny read-only book catalog; replace with a real datasource later."""

from dataclasses import dataclass
import json
from pathlib import Path
from urllib.parse import urlsplit

from .deeplink import valid_book_id


@dataclass(frozen=True)
class Book:
    title: str
    description: str
    url: str | None = None


def load_books(path: str | Path) -> dict[str, Book]:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Book catalog not found: {source}")
    data = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Book catalog must be a JSON object")
    books = {}
    for identifier, obj in data.items():
        if not isinstance(identifier, str) or not valid_book_id(identifier):
            raise ValueError("Book identifier must contain 1-48 ASCII letters/digits/_/-")
        if not isinstance(obj, dict):
            raise ValueError(f"Book '{identifier}' must be an object")
        title = obj.get("title")
        desc = obj.get("description", "")
        url = obj.get("url")
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ValueError(f"Invalid title for book '{identifier}'")
        if not isinstance(desc, str) or len(desc) > 2000:
            raise ValueError(f"Invalid description for book '{identifier}'")
        if url is not None:
            if not isinstance(url, str):
                raise ValueError(f"Invalid URL for book '{identifier}'")
            parsed = urlsplit(url)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                    or parsed.password or len(url) > 2000):
                raise ValueError(f"Book '{identifier}' URL must use public HTTPS")
        books[identifier] = Book(title.strip(), desc.strip(), url)
    return books
