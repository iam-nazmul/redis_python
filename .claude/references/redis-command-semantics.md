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
| `LPUSH key el [el ...]` | `:<new length>\r\n` |
| `LRANGE key start stop` | array of elements, `*0\r\n` when the range is empty or the key is missing |
| `LLEN key` | `:<length>\r\n`, `:0\r\n` for a missing key |
| `LPOP key` | bulk string of the first element, `$-1\r\n` when the list is missing or empty |
| `LPOP key count` | array of up to `count` elements, `*-1\r\n` for a missing key **or a count of 0** |
| `BLPOP key [key ...] timeout` | `*2\r\n` of [key, element], or `*-1\r\n` on timeout |
| `TYPE key` | `+string\r\n`, `+list\r\n`, `+stream\r\n`, or `+none\r\n` for a missing key |
| `XADD key id field value [...]` | bulk string of the id the entry was stored under |

`TYPE` is the one command that never answers `-WRONGTYPE`: reporting the type is
its purpose, so every type is a valid reply. Redis names seven — `string`, `list`,
`set`, `zset`, `hash`, `stream`, `vectorset` — and `Store.type_of` must gain a
branch for each type this server learns to store. `stream` is the third one here;
`set`, `zset`, `hash` and `vectorset` are still unimplemented.

### BLPOP

- Keys are tried in order; the first with an element answers immediately and the
  command never blocks. Otherwise the client is parked.
- The reply names the key it came from, which is why it is an array of two bulk
  strings rather than a bare element.
- `timeout` is in **seconds** and may be fractional; `0` waits indefinitely. A
  negative timeout is `-ERR timeout is negative`, a non-numeric one is
  `-ERR timeout is not a float or out of range`.
- One push wakes exactly one waiter, and it goes to the client that has been
  waiting longest.
- A parked client's later commands must not run until it is answered; queue them
  and run them in order afterwards.

### XADD

- The reply is the id the entry was stored under, as a **bulk string** — not a
  simple string. It is the *normalised* id, so `005-007` is stored and answered
  as `5-7`.
- Arity is `key id field value ...`: at least five arguments, and an odd number
  of them. A field without a value is `-ERR wrong number of arguments for 'xadd'
  command`, the same reply as too few arguments.
- A malformed id is `-ERR Invalid stream ID specified as stream command
  argument`. The id is parsed **before** the key is looked at, so an invalid id
  neither creates the stream nor reports `-WRONGTYPE` on a key of another type.
- Ids are unbounded here rather than 64-bit: Python ints do not overflow, so an
  id past `2**64` round-trips instead of wrapping as it would in real Redis.
- **Only explicit `<ms>-<seq>` ids are accepted for now.** Real Redis also takes
  `*` and `<ms>-*` and generates the missing parts; until this server does, both
  are rejected as invalid ids. Ordering is not enforced yet either — real Redis
  requires each id to be greater than the last and rejects `0-0`.
- Fields keep their insertion order and repeated fields are not merged, which is
  what a later `XRANGE` has to replay.

### LPOP count

- The reply type follows the *form*, not the outcome: with a count, popping one
  element still returns a one-element array, never a bulk string.
- A count larger than the list returns every element rather than erroring.
- Since Redis 7.2 both a missing key and `count` 0 answer a **null array**
  (`*-1\r\n`), not an empty array — the one edge case here that is genuinely
  counter-intuitive.
- A negative count is `-ERR value is out of range, must be positive`; a
  non-integer count is `-ERR value is not an integer or out of range`. Neither
  removes anything. (The negative-count string is from the Redis source's usual
  wording and has not been diffed against a live server.)

Popping the last element **deletes the key**: an empty list does not exist in
Redis, so afterwards `LLEN` is `:0`, `GET` is null rather than a wrong-type error,
and a later push recreates the key from scratch. `Store.pop_left` handles this;
any future command that removes elements must do the same.

`LPUSH` pushes each element onto the head in turn, so the arguments end up at the
front **in reverse**: `LPUSH k a b c` leaves `[c, b, a]`. `RPUSH` and `LPUSH` grow
opposite ends of the same list and can be mixed freely on one key.

### LRANGE indexes

- `stop` is **inclusive**: `LRANGE k 0 1` returns two elements.
- Negative indexes count from the end; `-1` is the last element.
- Both ends are clamped, never rejected: `LRANGE k -100 100` returns the whole
  list, and `LRANGE k 5 10` on a 3-element list returns `*0\r\n`.
- A missing key is an empty array, not an error — and reading it must **not**
  create the key (`Store.get_list`, not `get_or_create_list`).
- A non-integer index is `-ERR value is not an integer or out of range`.

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

- **`RPOP key [count]`** — the same as `LPOP` from the tail.
- **`LINDEX key index`** — bulk string, or null bulk string when the index is out
  of range. Negative indexes count from the end.

## Expiry

Expiry here is **passive**: a key is checked, and deleted, only when something
looks at it (`Store.get`). Real Redis also runs an active sampling cycle to
reclaim keys nobody reads. Without one, a set-and-never-read key holds memory
forever — a memory concern, not a correctness one, since no read can ever observe
an expired value.
