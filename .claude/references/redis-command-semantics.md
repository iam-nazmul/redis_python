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
| `XADD key id field value [...]` | bulk string of the id the entry was stored under; `id` may be `<ms>-<seq>`, `<ms>-*` or `*` |
| `XRANGE key start end` | array of `[id, [field, value, ...]]` pairs, `*0\r\n` when nothing matches; a bound may be `<ms>`, `<ms>-<seq>`, `-`, or `+` as the end |
| `XREAD STREAMS key [key ...] id [id ...]` | array of `[key, [entries]]`, one per stream with something newer, or `*-1\r\n` when none has |

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
- Fields keep their insertion order and repeated fields are not merged, which is
  what a later `XRANGE` has to replay.
- Ids are unbounded here rather than 64-bit: Python ints do not overflow, so an
  id past `2**64` round-trips instead of wrapping as it would in real Redis.
- `<ms>-*` leaves the sequence to the server and a bare `*` leaves the time part
  to it as well. A star stands in for a whole part or not at all: `*-*`, `5-1*`
  and `5-**` are invalid ids, not requests to generate something.

Ids must advance, and the two ways they can fail have **different** replies:

- Not greater than the last id — equal, an earlier millisecond, or the same
  millisecond with an earlier sequence — is `-ERR The ID specified in XADD is
  equal or smaller than the target stream top item`. A later millisecond may
  restart the sequence at 0; only the pair as a whole has to increase.
- `0-0` is `-ERR The ID specified in XADD must be greater than 0-0`, on an empty
  stream and a populated one alike. `0-1` is the smallest id any stream accepts.

Both id checks run **before the key is looked at**, so on a key of another type a
malformed id, or `0-0`, is reported instead of `-WRONGTYPE`. That is the order in
the Redis source — the `0-0` check returns early precisely so a doomed append
cannot leave an empty stream behind — and it has not been diffed against a live
server. A refused append leaves the stream unchanged and still usable.

An empty stream starts at `0-0`, tracked as `Stream.last_id` rather than read
from the last entry: it is a high-water mark, so a stream later emptied by `XDEL`
must keep refusing the ids it has already handed out.

### XADD generated ids

`<ms>-*` continues the sequence if the stream is already on that millisecond, and
starts a millisecond it has not reached at 0. Redis documents a third rule — a
time part of 0 starts at 1 — but it needs no case of its own: an empty stream's
last id is `0-0`, so millisecond 0 is one it has already reached, and continuing
from sequence 0 gives 1. `0-*` on a stream already holding `0-5` therefore yields
`0-6` rather than restarting at 1, which is what real Redis does too.

A generated sequence never rescues a millisecond behind the last id: `4-*` on a
stream at `5-5` is the ordinary too-small error, since generation produces an id
and `append` judges it like any other. Nor can generation produce `0-0`, so the
minimum-id error stays reachable only through an explicit `0-0`.

A bare `*` takes the time part from the **wall clock** — `unix_ms`, not the
monotonic `now_ms` expiry uses, because a stream id is a timestamp clients read
and compare against their own clock. The sequence then follows the same rule, so
appends inside one millisecond come out `…-0`, `…-1`, `…-2`.

`*` is the one id form that **cannot fail**. The clock reading is clamped forward
to the last id's millisecond before the sequence rule applies, so a stream holding
an explicit id from the future, or a clock that has moved backwards, still yields
an id that advances: `*` on a stream whose last id is `9999999999999999-0` answers
`9999999999999999-1` rather than the too-small error.

### XRANGE

- **Both ends are inclusive**, and each may be a bare `<ms>`, which stands for
  every sequence in that millisecond. A missing sequence therefore means 0 on the
  start and "the rest of the millisecond" on the end.
- The reply is an array of two-element arrays: the id as a bulk string, then a
  **flat** array of field, value, field, value — not pairs of pairs. Fields keep
  the order they were added in, repeats included.
- A missing key is an empty array, not an error, and querying must not create it.
  A range that matches nothing is the same empty array.
- `start` after `end` is an empty array rather than an error.
- A malformed bound is `-ERR Invalid stream ID specified as stream command
  argument`, reported before the key's type, as in `XADD`.
- `-` stands for the smallest id a stream can hold, `0-0`. Redis accepts it as
  **either** bound, not only as the start, so it is resolved during parsing
  rather than by position: `XRANGE k 0 -` is a valid, always-empty query.
- `+` runs to the last entry. It is **the end bound only**, and that asymmetry
  with `-` is real rather than an oversight: `-` is the id `0-0`, while `+` names
  no id here at all, since ids are unbounded. As an end it drops the upper bound;
  as a start it would have to bound the range *above* every entry, which needs a
  largest id this server does not have. Real Redis, whose ids stop at
  `UINT64_MAX`, accepts `+` as a start and answers the empty array it degenerates
  to; this server reports an invalid id.
- **`COUNT` is not supported**; `XRANGE key start end COUNT n` caps the reply in
  real Redis and is a wrong-arity error here.

Internally the end bound is turned into the id *just past* the last one wanted —
`5-3` becomes `5-4`, a bare `5` becomes `6-0` — so `Stream.range` can take a
half-open interval. That avoids inventing a largest sequence for the bare form, which
has no obvious value here: unlike real Redis, sequences are unbounded ints.

### XREAD

- **Exclusive**, where `XRANGE` is inclusive: `XREAD STREAMS k 5-0` returns the
  entries *after* `5-0`. A bare `<ms>` means sequence 0, so `k 6` excludes `6-0`
  itself — the same id, read differently by the two commands.
- The reply nests one level deeper than `XRANGE`: an array of streams, each
  `[key, [entry, ...]]`, **in the order the keys were asked for**. A stream with
  nothing new is left out rather than reported empty, so the reply may be shorter
  than the request; when no stream has anything, it is the **null array**
  `*-1\r\n` rather than an empty one. A blocking `XREAD` will use the same reply
  for a timeout.
- All the keys come first and all the ids follow, lining up by position:
  `STREAMS a b 0 5` reads `a` from `0` and `b` from `5`. A key may be repeated,
  and is then read once per id given for it. A missing key is simply absent from
  the reply.
- One bad argument fails the whole command: a malformed id anywhere is the
  invalid-id error and nothing is read, and a key of the wrong type anywhere is
  `-WRONGTYPE`, even when other streams had entries to report.
- Ids are strict: no `-`, `+` or `*`. Those are `-ERR Invalid stream ID specified
  as stream command argument`.
- Not `STREAMS` where it is expected is `-ERR syntax error`; an odd number of
  keys and ids after it is `-ERR Unbalanced XREAD list of streams: for each
  stream key an ID or '$' must be specified.` (that string is from the Redis
  source's wording and has not been diffed against a live server). Fewer than
  three arguments is the wrong-arity error, which is what `XREAD STREAMS k` gets.
- **Not supported yet**: `COUNT` and `BLOCK`, both a `-ERR syntax error` for now,
  and `$` as an id, which is an invalid id rather than a syntax error since it is
  parsed as one.

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
