"""Command implementations and the dispatch table."""

from collections.abc import Callable
from dataclasses import dataclass

from app import resp
from app.store import (
    MIN_ENTRY_ID,
    EntryId,
    Store,
    Stream,
    StreamEntry,
    StreamOrderError,
    WrongTypeError,
    now_ms,
    unix_ms,
)


@dataclass
class Block:
    """A command that cannot answer yet.

    Returned instead of a reply when a client must wait: the server parks the
    connection on *keys* and answers later, either when one of them gains an
    element or when the timeout expires.
    """

    keys: list[bytes]
    timeout: float  # seconds; 0 blocks indefinitely


Reply = bytes | Block
Handler = Callable[[Store, resp.Command], Reply]

_HANDLERS: dict[bytes, Handler] = {}

PONG = resp.simple_string(b"PONG Nazmul")
SYNTAX_ERROR = resp.error(b"ERR syntax error")
NOT_AN_INTEGER = resp.error(b"ERR value is not an integer or out of range")
INVALID_EXPIRY = resp.error(b"ERR invalid expire time in 'set' command")
OUT_OF_RANGE = resp.error(b"ERR value is out of range, must be positive")
NEGATIVE_TIMEOUT = resp.error(b"ERR timeout is negative")
INVALID_ENTRY_ID = resp.error(
    b"ERR Invalid stream ID specified as stream command argument"
)
ENTRY_ID_TOO_SMALL = resp.error(
    b"ERR The ID specified in XADD is equal or smaller than the target stream top item"
)
ENTRY_ID_AT_MINIMUM = resp.error(
    b"ERR The ID specified in XADD must be greater than 0-0"
)
INVALID_TIMEOUT = resp.error(b"ERR timeout is not a float or out of range")
WRONG_TYPE = resp.error(
    b"WRONGTYPE Operation against a key holding the wrong kind of value"
)


def command(name: bytes) -> Callable[[Handler], Handler]:
    """Register a handler under the command it implements."""

    def register(handler: Handler) -> Handler:
        _HANDLERS[name] = handler
        return handler

    return register


def wrong_args(name: bytes) -> bytes:
    return resp.error(b"ERR wrong number of arguments for '%s' command" % name.lower())


def execute(store: Store, args: resp.Command) -> Reply:
    """Run one command and return the reply, or a Block if it must wait."""
    name = args[0].upper()
    handler = _HANDLERS.get(name)
    if handler is None:
        return resp.error(b"ERR unknown command '%s'" % args[0])
    try:
        return handler(store, args)
    except WrongTypeError:
        return WRONG_TYPE


@command(b"PING")
def ping(store: Store, args: resp.Command) -> bytes:
    return PONG


@command(b"ECHO")
def echo(store: Store, args: resp.Command) -> bytes:
    if len(args) != 2:
        return wrong_args(b"ECHO")
    return resp.bulk_string(args[1])


def _parse_set_options(
    options: resp.Command,
) -> tuple[float | None, bytes | None, bytes | None]:
    """Read SET's trailing options, which may appear in any order.

    Returns (expires_at, condition, error); a non-None error means nothing should
    be written.
    """
    expires_at: float | None = None
    condition: bytes | None = None
    i = 0
    while i < len(options):
        option = options[i].upper()
        if option in (b"EX", b"PX"):
            if expires_at is not None or i + 1 >= len(options):
                return None, None, SYNTAX_ERROR
            try:
                amount = int(options[i + 1])
            except ValueError:
                return None, None, NOT_AN_INTEGER
            if amount <= 0:
                return None, None, INVALID_EXPIRY
            expires_at = now_ms() + (amount * 1000 if option == b"EX" else amount)
            i += 2
        elif option in (b"NX", b"XX"):
            if condition is not None:
                return None, None, SYNTAX_ERROR
            condition = option
            i += 1
        else:
            return None, None, SYNTAX_ERROR
    return expires_at, condition, None


@command(b"SET")
def set_(store: Store, args: resp.Command) -> bytes:
    if len(args) < 3:
        return wrong_args(b"SET")
    key, value = args[1], args[2]
    expires_at, condition, error = _parse_set_options(args[3:])
    if error is not None:
        return error
    # An expired key counts as absent, so NX can take it over.
    exists = key in store
    if (condition == b"NX" and exists) or (condition == b"XX" and not exists):
        return resp.NULL
    store.set(key, value, expires_at)
    return resp.OK


@command(b"GET")
def get(store: Store, args: resp.Command) -> bytes:
    if len(args) != 2:
        return wrong_args(b"GET")
    value = store.get_string(args[1])
    if value is None:
        return resp.NULL
    return resp.bulk_string(value)


@command(b"RPUSH")
def rpush(store: Store, args: resp.Command) -> bytes:
    if len(args) < 3:
        return wrong_args(b"RPUSH")
    entries = store.get_or_create_list(args[1])
    entries.extend(args[2:])
    return resp.integer(len(entries))


@command(b"LPUSH")
def lpush(store: Store, args: resp.Command) -> bytes:
    if len(args) < 3:
        return wrong_args(b"LPUSH")
    entries = store.get_or_create_list(args[1])
    # Each element is pushed onto the head in turn, so the arguments end up at the
    # front in reverse order: LPUSH k a b c leaves [c, b, a].
    entries[:0] = reversed(args[2:])
    return resp.integer(len(entries))


def _absolute(index: int, length: int) -> int:
    """Turn a possibly negative list index into an offset from the head."""
    return length + index if index < 0 else index


@command(b"LRANGE")
def lrange(store: Store, args: resp.Command) -> bytes:
    if len(args) != 4:
        return wrong_args(b"LRANGE")
    try:
        start, stop = int(args[2]), int(args[3])
    except ValueError:
        return NOT_AN_INTEGER
    entries = store.get_list(args[1])
    length = len(entries)
    # Both ends are clamped rather than rejected: Redis answers an out-of-range
    # request with whatever part of the range exists, and an empty array if none.
    start = max(_absolute(start, length), 0)
    stop = min(_absolute(stop, length), length - 1)
    if start > stop:
        return resp.array([])
    return resp.array(entries[start : stop + 1])


@command(b"LPOP")
def lpop(store: Store, args: resp.Command) -> bytes:
    if len(args) not in (2, 3):
        return wrong_args(b"LPOP")
    if len(args) == 2:
        value = store.pop_left(args[1])
        return resp.NULL if value is None else resp.bulk_string(value)
    try:
        count = int(args[2])
    except ValueError:
        return NOT_AN_INTEGER
    if count < 0:
        return OUT_OF_RANGE
    popped = store.pop_left_many(args[1], count)
    # Since Redis 7.2 the count form answers a null array both for a missing key
    # and for a count of zero, rather than an empty array.
    if not popped:
        return resp.NULL_ARRAY
    return resp.array(popped)


@command(b"TYPE")
def type_(store: Store, args: resp.Command) -> bytes:
    if len(args) != 2:
        return wrong_args(b"TYPE")
    # Redis names seven types; this server stores two, and reports "none" for a
    # key that does not exist rather than treating it as an error.
    return resp.simple_string(store.type_of(args[1]))


@command(b"BLPOP")
def blpop(store: Store, args: resp.Command) -> Reply:
    if len(args) < 3:
        return wrong_args(b"BLPOP")
    *keys, raw_timeout = args[1:]
    try:
        timeout = float(raw_timeout)
    except ValueError:
        return INVALID_TIMEOUT
    if timeout < 0:
        return NEGATIVE_TIMEOUT
    # Keys are tried in order; the first one holding an element answers at once.
    popped = pop_first_available(store, keys)
    return popped if popped is not None else Block(keys, timeout)


def pop_first_available(store: Store, keys: list[bytes]) -> bytes | None:
    """Pop from the first of *keys* that has an element, as a [key, value] array.

    Returns None when every key is empty. Shared with the server, which retries
    this for a blocked client each time the keyspace changes.
    """
    for key in keys:
        value = store.pop_left(key)
        if value is not None:
            return resp.array([key, value])
    return None


@command(b"LLEN")
def llen(store: Store, args: resp.Command) -> bytes:
    if len(args) != 2:
        return wrong_args(b"LLEN")
    # A missing key is an empty list, so this is 0 rather than an error.
    return resp.integer(len(store.get_list(args[1])))


@dataclass(frozen=True)
class RequestedEntryId:
    """An entry id as the client wrote it, with the parts it left to the server.

    Both parts may be left out: "<ms>-*" gives up the sequence, and a bare "*"
    gives up the time part too, which is the only case the clock is read for.
    """

    milliseconds: int | None  # None when the client wrote a bare "*"
    sequence: int | None  # None when the client wrote "*" in its place

    def explicit_id(self) -> EntryId | None:
        """The id the client spelled out, or None when it left a part to us."""
        if self.milliseconds is None or self.sequence is None:
            return None
        return EntryId(self.milliseconds, self.sequence)

    def resolve(self, stream: Stream) -> EntryId:
        """The id to store under, generating whichever parts were left out."""
        explicit = self.explicit_id()
        if explicit is not None:
            return explicit
        if self.milliseconds is None:
            return stream.next_id_from_clock(unix_ms())
        return stream.next_id(self.milliseconds)


def _parse_entry_id(raw: bytes) -> RequestedEntryId | None:
    """Parse "<ms>-<seq>", "<ms>-*" or a bare "*", or None when it is none of them.

    A star stands in for a whole part or not at all: "*-*", "5-1*" and "5-**" are
    invalid rather than requests to generate something.
    """
    if raw == b"*":
        return RequestedEntryId(None, None)
    milliseconds, separator, sequence = raw.partition(b"-")
    if not (separator and milliseconds.isdigit()):
        return None
    if sequence == b"*":
        return RequestedEntryId(int(milliseconds), None)
    if not sequence.isdigit():
        return None
    return RequestedEntryId(int(milliseconds), int(sequence))


@command(b"XADD")
def xadd(store: Store, args: resp.Command) -> bytes:
    # key, id and at least one field/value pair, so an odd count of five or more.
    if len(args) < 5 or len(args) % 2 == 0:
        return wrong_args(b"XADD")
    requested = _parse_entry_id(args[2])
    if requested is None:
        return INVALID_ENTRY_ID
    # Both id checks happen before the key is touched, and so ahead of -WRONGTYPE:
    # that is Redis' own order, and it keeps a rejected command from creating the
    # stream it would then have to fail on, leaving an empty key behind. Only an
    # explicit 0-0 can trip this; a generated sequence never lands there.
    if requested.explicit_id() == MIN_ENTRY_ID:
        return ENTRY_ID_AT_MINIMUM
    stream = store.get_or_create_stream(args[1])
    entry_id = requested.resolve(stream)
    try:
        stream.append(entry_id, args[3:])
    except StreamOrderError:
        return ENTRY_ID_TOO_SMALL
    return resp.bulk_string(entry_id.encode())


# "-" stands for the smallest id a stream can hold. Redis takes it as either
# bound, not only as the start, so it is resolved here rather than positionally.
RANGE_MINIMUM = b"-"
# "+" stands for the largest, which has no equivalent here: ids are unbounded, so
# there is no id to name. It becomes a range with no upper bound, which works
# only as the end — see _parse_range.
RANGE_MAXIMUM = b"+"


def _after(entry_id: EntryId) -> EntryId:
    """The id immediately after *entry_id*, which turns an inclusive end exclusive."""
    return EntryId(entry_id.milliseconds, entry_id.sequence + 1)


def _parse_range_start(raw: bytes) -> EntryId | None:
    """The inclusive lower bound of an XRANGE, or None when it is not an id.

    A bare "<ms>" means the whole millisecond, so its sequence defaults to 0.
    """
    if raw == RANGE_MINIMUM:
        return MIN_ENTRY_ID
    milliseconds, _, sequence = raw.partition(b"-")
    if not milliseconds.isdigit():
        return None
    if not sequence:
        return EntryId(int(milliseconds), 0)
    if not sequence.isdigit():
        return None
    return EntryId(int(milliseconds), int(sequence))


def _parse_range_end(raw: bytes) -> EntryId | None:
    """The **exclusive** upper bound of an XRANGE, or None when it is not an id.

    XRANGE's end is inclusive, and a bare "<ms>" includes every sequence in that
    millisecond. Rather than invent a largest sequence to stand in for "every" —
    sequences here have no ceiling — each form is turned into the id just past
    the last one it asks for: "5-3" ends before 5-4, and "5" before 6-0.
    """
    if raw == RANGE_MINIMUM:
        return _after(MIN_ENTRY_ID)
    milliseconds, _, sequence = raw.partition(b"-")
    if not milliseconds.isdigit():
        return None
    if not sequence:
        return EntryId(int(milliseconds) + 1, 0)
    if not sequence.isdigit():
        return None
    return _after(EntryId(int(milliseconds), int(sequence)))


def _encode_entry(entry: StreamEntry) -> bytes:
    """One entry as its id followed by a flat array of its field/value pairs."""
    return resp.array_of(
        [resp.bulk_string(entry.id.encode()), resp.array(entry.fields)]
    )


def _parse_range(start: bytes, end: bytes) -> tuple[EntryId, EntryId | None] | None:
    """Both bounds of an XRANGE, or None when either is malformed.

    The end is None for "+", a range with no upper bound. Unlike "-", which is
    the real id 0-0, "+" names no id at all here, so it can only be the end: as a
    start it would have to bound the range above every entry, which needs a
    largest id this server does not have. Real Redis, whose ids stop at
    UINT64_MAX, accepts it there too and answers the empty array it degenerates
    to; this server reports an invalid id instead.
    """
    parsed_start = _parse_range_start(start)
    if parsed_start is None:
        return None
    if end == RANGE_MAXIMUM:
        return parsed_start, None
    parsed_end = _parse_range_end(end)
    if parsed_end is None:
        return None
    return parsed_start, parsed_end


@command(b"XRANGE")
def xrange(store: Store, args: resp.Command) -> bytes:
    if len(args) != 4:
        return wrong_args(b"XRANGE")
    # Both bounds are parsed before the key is looked at, as in XADD, so a
    # malformed id is reported ahead of -WRONGTYPE.
    bounds = _parse_range(args[2], args[3])
    if bounds is None:
        return INVALID_ENTRY_ID
    start, end = bounds
    entries = store.get_stream(args[1]).range(start, end)
    return resp.array_of([_encode_entry(entry) for entry in entries])
