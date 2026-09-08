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

```bash
pkill -9 -f "^python -u main.py"; sleep 0.3
python -u main.py > /tmp/bedis.log 2>&1 &
SERVER_PID=$!
sleep 1
python .claude/skills/verify-server/scripts/redis_check.py checks.json
kill $SERVER_PID
```

The harness exits non-zero if any check with an `expect` failed, so it can gate a
commit. Read `/tmp/bedis.log` when a connection behaves oddly — the server logs
each accept, close, and protocol error.

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
