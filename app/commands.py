"""Command implementations and the dispatch table."""

from collections.abc import Callable

from app import resp
from app.store import Store, WrongTypeError, now_ms

Handler = Callable[[Store, resp.Command], bytes]

_HANDLERS: dict[bytes, Handler] = {}

PONG = resp.simple_string(b"PONG Nazmul")
SYNTAX_ERROR = resp.error(b"ERR syntax error")
NOT_AN_INTEGER = resp.error(b"ERR value is not an integer or out of range")
INVALID_EXPIRY = resp.error(b"ERR invalid expire time in 'set' command")
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


def execute(store: Store, args: resp.Command) -> bytes:
    """Run one command and return the reply to send back."""
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
