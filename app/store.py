"""The keyspace: values, their optional expiries and type checks."""

import time
from dataclasses import dataclass, field


@dataclass(frozen=True, order=True)
class EntryId:
    """A stream entry's id: a millisecond timestamp and a sequence within it.

    Ordered, because a stream keeps its entries in ascending id order and later
    stages have to reject an id that does not advance past the last one.
    """

    milliseconds: int
    sequence: int

    def encode(self) -> bytes:
        return b"%d-%d" % (self.milliseconds, self.sequence)


@dataclass
class StreamEntry:
    id: EntryId
    # Flat field/value pairs, kept in the order they were given: a stream entry
    # is an ordered map, and repeated fields are preserved rather than merged.
    fields: list[bytes]


# The id every stream starts from: the first entry must beat it, which is why
# Redis rejects an explicit 0-0 outright.
MIN_ENTRY_ID = EntryId(0, 0)


@dataclass
class Stream:
    """A sequence of entries in ascending id order.

    A class of its own rather than a plain list, so that the accessors can tell a
    stream from a list and answer -WRONGTYPE for the commands of the other type.
    """

    entries: list[StreamEntry] = field(default_factory=list)
    # Tracked apart from entries[-1] because it is the high-water mark, not the
    # last element: a stream emptied by a future XDEL still refuses ids below it.
    last_id: EntryId = MIN_ENTRY_ID

    def append(self, entry_id: EntryId, fields: list[bytes]) -> EntryId:
        """Append an entry, or raise StreamOrderError if the id does not advance."""
        if entry_id <= self.last_id:
            raise StreamOrderError(entry_id)
        self.entries.append(StreamEntry(entry_id, fields))
        self.last_id = entry_id
        return entry_id


# Redis values are typed; so far this clone stores strings, lists and streams.
Value = bytes | list[bytes] | Stream


class WrongTypeError(Exception):
    """Raised when a command is used on a key holding a different type."""


class StreamOrderError(Exception):
    """Raised when an entry id is not strictly greater than the stream's last id."""


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

    def type_of(self, key: bytes) -> bytes:
        """Return the Redis type name at *key*, or b"none" when it does not exist.

        The one accessor that never raises WrongTypeError: reporting the type is
        the whole point, so every type is a valid answer.
        """
        value = self.get(key)
        if value is None:
            return b"none"
        if isinstance(value, Stream):
            return b"stream"
        return b"list" if isinstance(value, list) else b"string"

    def get_string(self, key: bytes) -> bytes | None:
        value = self.get(key)
        if value is not None and not isinstance(value, bytes):
            raise WrongTypeError(key)
        return value

    def set(self, key: bytes, value: Value, expires_at: float | None = None) -> None:
        self._entries[key] = Entry(value, expires_at)

    def get_list(self, key: bytes) -> list[bytes]:
        """Return the list at *key*, or an empty list when the key is absent.

        Read-only: unlike get_or_create_list it never creates the key, because a
        command that only reads a list must not bring one into existence.
        """
        value = self.get(key)
        if value is None:
            return []
        if not isinstance(value, list):
            raise WrongTypeError(key)
        return value

    def pop_left(self, key: bytes) -> bytes | None:
        """Remove and return the first element, or None if there is none to pop."""
        popped = self.pop_left_many(key, 1)
        return popped[0] if popped else None

    def pop_left_many(self, key: bytes, count: int) -> list[bytes] | None:
        """Remove and return up to *count* elements from the head.

        Returns None when the key holds no list, which the caller reports as a
        null reply. A list emptied by the pop is deleted along with its key: in
        Redis an empty list does not exist, so the key must stop existing with it.
        """
        entries = self.get_list(key)
        if not entries:
            return None
        popped = entries[:count]
        del entries[:count]
        if not entries:
            del self._entries[key]
        return popped

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

    def get_or_create_stream(self, key: bytes) -> Stream:
        """Return the stream at *key*, creating an empty one if the key is absent.

        Like get_or_create_list the stream is returned by reference, so appending
        an entry leaves any expiry the key already had untouched.
        """
        value = self.get(key)
        if value is None:
            value = Stream()
            self._entries[key] = Entry(value)
        elif not isinstance(value, Stream):
            raise WrongTypeError(key)
        return value
