"""Strict, deterministic routing for Telegram /start parameters."""

from dataclasses import dataclass
import re

_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,64}$", re.ASCII)
_BOOK_ID = re.compile(r"^[A-Za-z0-9_-]{1,48}$", re.ASCII)


@dataclass(frozen=True)
class Route:
    kind: str
    identifier: str = ""


def route_start(payload: str | None) -> Route:
    """A link is navigation, NOT authorization to a protected resource."""
    if not payload:
        return Route("home")
    if not _TOKEN.fullmatch(payload):
        return Route("invalid")
    if payload == "help":
        return Route("help")
    if payload.startswith("book_"):
        identifier = payload[5:]
        if _BOOK_ID.fullmatch(identifier):
            return Route("book", identifier)
    return Route("invalid")


def valid_book_id(value: str | None) -> bool:
    return bool(value and _BOOK_ID.fullmatch(value))
