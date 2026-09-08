---
name: verify-server
description: Start the bedis server and exercise it over RESP with a reusable check harness. Use when asked to run or test the server, after changing anything under app/, or before committing a new command.
---

# Verify the server

Boot the server, run a battery of RESP checks against it, shut it down. The
harness is `scripts/redis_check.py`; the reply strings to assert are in
`.claude/references/redis-command-semantics.md`.

## Two footguns — read before running anything

**1. `pkill -f main.py` kills the shell running your test script.** With `-f`,
pkill matches the whole command line, and your script's command line contains the
string `main.py`. The test dies before it reports anything. Capture the PID
instead, or anchor the pattern so it can only match the server:

```bash
pkill -9 -f "^python -u main.py"   # anchored: matches the server, not your shell
```

**2. `reuse_port=True` lets a second server bind the same port.** A leftover
server from an earlier run does not cause "address in use" — both processes bind
and the kernel load-balances connections between them, so roughly half your
checks hit the old code. Symptom: a change appears to have no effect, or results
alternate between runs. Always kill stale servers first, and if results look
impossible, check `pgrep -af "^python -u main.py"` before debugging the code.

## Procedure

Run on a port of your own rather than 6389, so a server someone else is running
by hand cannot absorb half your connections. `reuse_port=False` makes a collision
fail loudly instead of silently sharing the port:

```bash
python -u -c "import logging; logging.basicConfig(level=logging.INFO); from app.server import Server; Server(port=6399, reuse_port=False).serve_forever()" \
  > /tmp/bedis.log 2>&1 &
SERVER_PID=$!
sleep 1
python .claude/skills/verify-server/scripts/redis_check.py --port 6399 checks.json
kill $SERVER_PID
```

**Restart the server between fixtures.** Every fixture assumes an empty keyspace
and they share key names, so running two against one server makes the second fail
on state the first left behind — a false regression that looks alarming. Loop:

```bash
for f in lpush rpush lrange; do
  python -u -c "import logging; logging.basicConfig(level=logging.INFO); from app.server import Server; Server(port=6399, reuse_port=False).serve_forever()" \
    > /tmp/bedis.log 2>&1 &
  pid=$!; sleep 0.8
  python .claude/skills/verify-server/scripts/redis_check.py --port 6399 tests/checks/$f.json
  echo "$f exit=$?"
  kill $pid; wait $pid 2>/dev/null; sleep 0.3
done
```

The harness exits non-zero if any check with an `expect` failed, so it can gate a
commit — but capture `$?` directly into a variable. Reading it after a pipe
(`... | tail -1`) or inside `$(...)` reports that command's status, not the
harness's, and silently turns a failing run into a passing one.

Read `/tmp/bedis.log` when a connection behaves oddly — the server logs each
accept, close, and protocol error.

## Writing checks

A checks file is a JSON list; see the harness docstring for every entry type.

```json
[
  {"send": ["SET", "k", "v", "PX", "100"], "expect": "+OK\r\n"},
  {"sleep": 0.15},
  {"send": ["GET", "k"], "expect": "$-1\r\n", "label": "expired"},
  {"send": ["GET", "k"], "client": "b", "label": "from a second connection"},
  {"raw": "*x\r\n", "expect": "-ERR Protocol error\r\n", "client": "bad"}
]
```

`client` opens a named extra connection — use it for keyspace sharing and for
malformed input, which closes the connection it arrives on.

### Blocking commands

A blocking command has no reply to wait for, so send and read are separated:

```json
[
  {"send": ["BLPOP", "k", "0"], "client": "b", "read": false},
  {"recv": "b", "timeout": 0.3, "expect": "", "label": "still blocked"},
  {"send": ["RPUSH", "k", "v"], "expect": ":1\r\n"},
  {"recv": "b", "expect": "*2\r\n$1\r\nk\r\n$1\r\nv\r\n", "label": "woken"}
]
```

`"read": false` leaves the reply pending; a later `recv` collects it. An `expect`
of `""` on a `recv` asserts that **nothing** arrived before the timeout, which is
how you prove a client is still parked rather than silently answered.

Two behaviours no checks file can express — verify them by hand when touching the
blocking path: a client that disconnects while parked must be dropped (the next
push should stay in the list rather than vanish into a dead waiter), and an idle
server holding waiters must burn no CPU (`/proc/<pid>/stat` utime+stime should not
move), proving the loop sleeps in `select` instead of spinning.

## Always include the transport regressions

Whatever command you are working on, keep these four. They break easily and no
per-command check catches them:

1. **Pipelining** — two commands in one packet get two replies, in order.
2. **Split command** — send `*3\r\n$3\r\nSET\r\n$5\r\nsplit`, pause, then send
   `\r\n$4\r\nokay\r\n`; the reply must be `+OK\r\n`.
3. **Malformed RESP** — one client sending `*x\r\n` gets `-ERR Protocol error`
   and is disconnected while other clients keep working.
4. **Binary-safe values** — a value containing `\r\n` and a NUL byte round-trips.

Checks 2 and 3 need raw sends; the harness `raw` entry does not currently split a
single command across two packets, so write that one inline with a socket when
you need it.
