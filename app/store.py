"""The keyspace: values, their optional expiries and type checks."""

import time
from dataclasses import dataclass

# Redis values are typed; so far this clone stores strings and lists.
Value = bytes | list[bytes]


class WrongTypeError(Exception):
    """Raised when a command is used on a key holding a different type."""


def now_ms() -> float:
    """The monotonic clock in milliseconds, unaffected by system clock changes."""
    return time.monotonic() * 1000


@dataclass
class Entry:
    value: Value
    expires_at: float | None = None

    def is_expired(self) -> bool:
        return self.expires_at is not None and now_ms() >= self.expires_at


class Store:
    """A keyspace with lazy expiry: a key dies the next time anyone looks at it."""

    def __init__(self) -> None:
        self._entries: dict[bytes, Entry] = {}

    def __contains__(self, key: bytes) -> bool:
        return self.get(key) is not None

    def get(self, key: bytes) -> Value | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.is_expired():
            del self._entries[key]
            return None
        return entry.value

    def get_string(self, key: bytes) -> bytes | None:
        value = self.get(key)
        if isinstance(value, list):
            raise WrongTypeError(key)
        return value

    def set(self, key: bytes, value: Value, expires_at: float | None = None) -> None:
        self._entries[key] = Entry(value, expires_at)

    def get_or_create_list(self, key: bytes) -> list[bytes]:
        """Return the list at *key*, creating an empty one if the key is absent.

        The list is returned by reference, so appending to it in place leaves any
        expiry the key already had untouched.
        """
        value = self.get(key)
        if value is None:
            value = []
            self._entries[key] = Entry(value)
        elif not isinstance(value, list):
            raise WrongTypeError(key)
        return value
