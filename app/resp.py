"""Encoding and decoding for the Redis serialization protocol (RESP)."""

CRLF = b"\r\n"

# A command is the list of bulk-string arguments a client sent, verb first.
Command = list[bytes]


class ProtocolError(ValueError):
    """Raised when a client sends bytes that are not valid RESP."""


def simple_string(value: bytes) -> bytes:
    return b"+%s%s" % (value, CRLF)


def error(message: bytes) -> bytes:
    return b"-%s%s" % (message, CRLF)


def integer(value: int) -> bytes:
    return b":%d%s" % (value, CRLF)


def bulk_string(value: bytes) -> bytes:
    return b"$%d%s%s%s" % (len(value), CRLF, value, CRLF)


OK = simple_string(b"OK")
NULL = b"$-1%s" % CRLF


def parse(buffer: bytes) -> tuple[list[Command], bytes]:
    """Split *buffer* into complete commands and the unconsumed tail.

    The tail is whatever belongs to a command that has not fully arrived yet; the
    caller keeps it and prepends it to the next read.
    """
    commands = []
    while buffer:
        command, rest = _parse_one(buffer)
        if command is None:
            break
        commands.append(command)
        buffer = rest
    return commands, buffer


def _read_line(buffer: bytes, start: int) -> tuple[bytes | None, int]:
    """Read up to the next CRLF, or return None if it has not arrived yet."""
    end = buffer.find(CRLF, start)
    if end == -1:
        return None, start
    return buffer[start:end], end + len(CRLF)


def _length(line: bytes) -> int:
    try:
        return int(line[1:])
    except ValueError:
        raise ProtocolError(f"invalid length header: {line!r}") from None


def _parse_one(buffer: bytes) -> tuple[Command | None, bytes]:
    """Parse the leading command, returning (None, buffer) if it is incomplete."""
    line, pos = _read_line(buffer, 0)
    if line is None:
        return None, buffer
    if not line.startswith(b"*"):
        # inline command, e.g. typed straight into telnet
        return line.split(), buffer[pos:]
    args = []
    for _ in range(_length(line)):
        header, pos = _read_line(buffer, pos)
        if header is None:
            return None, buffer
        if not header.startswith(b"$"):
            raise ProtocolError(f"expected a bulk string, got {header!r}")
        end = pos + _length(header)
        if len(buffer) < end + len(CRLF):
            return None, buffer
        args.append(buffer[pos:end])
        pos = end + len(CRLF)
    return args, buffer[pos:]
