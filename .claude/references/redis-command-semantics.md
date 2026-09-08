# Command semantics

Exact replies for the commands in `app/commands.py`, plus the ones this project
has not reached yet. Matching the real server's strings matters: the CodeCrafters
tests and `redis-cli` both compare them literally.

## Implemented

| Command | Reply |
|---------|-------|
| `PING` | `+PONG\r\n` — **this server answers `+PONG Nazmul\r\n`**, which real Redis and the CodeCrafters PING stage do not accept |
| `ECHO msg` | bulk string of `msg` |
| `SET key value [EX s\|PX ms] [NX\|XX]` | `+OK\r\n`, or `$-1\r\n` when NX/XX refuses the write |
| `GET key` | bulk string, or `$-1\r\n` when missing or expired |
| `RPUSH key el [el ...]` | `:<new length>\r\n` |

### SET options

- `EX <seconds>` / `PX <milliseconds>` — relative expiry. Mutually exclusive.
- `NX` — only if the key does not exist. `XX` — only if it does. Mutually exclusive.
- Options may appear in any order and are case-insensitive.
- A plain `SET` over an existing key **clears its TTL**.
- An expired-but-not-yet-collected key counts as absent, so `NX` may take it over.

### Errors

| Condition | Reply |
|-----------|-------|
| Wrong argument count | `-ERR wrong number of arguments for 'set' command\r\n` (verb lowercased) |
| Unknown option, `NX`+`XX`, `EX`+`PX`, dangling `EX` | `-ERR syntax error\r\n` |
| Non-numeric expiry | `-ERR value is not an integer or out of range\r\n` |
| Expiry `<= 0` | `-ERR invalid expire time in 'set' command\r\n` |
| Command on a key of another type | `-WRONGTYPE Operation against a key holding the wrong kind of value\r\n` |
| Unrecognised verb | `-ERR unknown command 'X'\r\n` |

On any error the keyspace must be left untouched.

## Not yet implemented

Semantics from the Redis docs, for when these stages come up:

- **`LPUSH key el [el ...]`** — prepends; each element goes to the head in turn,
  so `LPUSH k a b` yields `[b, a]`. Reply: new length as an integer.
- **`LLEN key`** — length as an integer; `:0\r\n` for a missing key (not an error).
- **`LRANGE key start stop`** — array of elements, `stop` inclusive. Negative
  indexes count from the end (`-1` is last). Out-of-range indexes are clamped, so
  `LRANGE k -100 100` returns the whole list and `LRANGE k 5 10` on a 3-element
  list returns an empty array `*0\r\n`. A missing key is an empty array, never an error.
- **`LPOP key [count]`** — without `count`: the element as a bulk string, `$-1\r\n`
  if missing. With `count`: an array of up to `count` elements; since Redis 7.2 a
  missing key returns a **null array** (`*-1\r\n`), not a null bulk string.
- **`LINDEX key index`** — bulk string, or null bulk string when the index is out
  of range. Negative indexes count from the end.
- **`TYPE key`** — `+string\r\n`, `+list\r\n`, or `+none\r\n` for a missing key.

## Expiry

Expiry here is **passive**: a key is checked, and deleted, only when something
looks at it (`Store.get`). Real Redis also runs an active sampling cycle to
reclaim keys nobody reads. Without one, a set-and-never-read key holds memory
forever — a memory concern, not a correctness one, since no read can ever observe
an expired value.
