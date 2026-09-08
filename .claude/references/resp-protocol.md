# RESP (Redis serialization protocol)

This server speaks RESP2. Every reply below is exactly what `app/resp.py` encodes;
if you are adding a command, use those helpers rather than writing byte literals.

## Reply types

| Type | Wire format | Helper in `app/resp.py` |
|------|-------------|-------------------------|
| Simple string | `+OK\r\n` | `simple_string(b"OK")`, `resp.OK` |
| Error | `-ERR message\r\n` | `error(b"ERR message")` |
| Integer | `:42\r\n` (signed, 64-bit) | `integer(42)` |
| Bulk string | `$5\r\nhello\r\n` | `bulk_string(b"hello")` |
| Null bulk string | `$-1\r\n` | `resp.NULL` |
| Array | `*2\r\n$1\r\na\r\n$1\r\nb\r\n` | not yet implemented — needed for LRANGE |
| Empty array | `*0\r\n` | — |
| Null array | `*-1\r\n` | — |

Bulk strings are binary-safe: the length prefix means a value may contain `\r\n`
or NUL bytes. Never parse or encode a value by splitting on a delimiter.

## Requests

Clients send an array of bulk strings — the verb first, then arguments:

```
*3\r\n$3\r\nSET\r\n$5\r\nmykey\r\n$7\r\nmyvalue\r\n
```

The parser also accepts **inline commands** (`PING\r\n`, whitespace-split), which
is what a raw `telnet`/`nc` session sends. Real Redis supports these too.

## Invariants the parser must preserve

Three properties are easy to break and each has a regression check in the
verify-server skill:

1. **A command can arrive split across packets.** TCP gives no message
   boundaries. `resp.parse()` returns `(commands, tail)` and the caller keeps the
   tail in the connection buffer until more bytes arrive.
2. **Several commands can arrive in one packet** (pipelining). Parse in a loop
   until no complete command remains; reply to each in order.
3. **Malformed input must not kill the process.** The server is single-threaded,
   so an uncaught exception takes down every connected client. `_length()` raises
   `resp.ProtocolError`; the server answers `-ERR Protocol error` and closes only
   that connection.

## RESP2 vs RESP3

RESP3 (`HELLO 3`) replaces the null bulk string with `_\r\n`, adds maps and
doubles, and changes some replies. This server is RESP2 only — if `HELLO` is ever
implemented, reply with an error for version 3 rather than half-supporting it.
