---
name: add-command
description: Implement a new Redis command in this codebase, following its dispatch, error, and storage conventions. Use when working a CodeCrafters stage or adding any verb to app/commands.py.
---

# Add a command

## 1. Look up the exact replies first

`.claude/references/redis-command-semantics.md` has the reply and error strings,
including the ones for commands not yet implemented. The tests compare literally
— `-ERR syntax error` and `-ERR Syntax error` are not the same result. If the
command is not in that file, check the Redis docs and add it there.

## 2. Write the handler

Handlers live in `app/commands.py` and register themselves with a decorator:

```python
@command(b"LLEN")
def llen(store: Store, args: resp.Command) -> bytes:
    if len(args) != 2:
        return wrong_args(b"LLEN")
    return resp.integer(len(store.get_or_create_list(args[1])))
```

- **Encode through `app/resp.py`** — `resp.integer`, `resp.bulk_string`,
  `resp.OK`, `resp.NULL`, `resp.error`. No byte literals in a handler.
- **Check arity first**, and return `wrong_args(b"VERB")`.
- **Reach the keyspace through a `Store` method**, never `store._entries`. The
  store raises `WrongTypeError` and `execute()` turns it into the `-WRONGTYPE`
  reply, so a handler needs no `isinstance` checks.
- **The verb is matched uppercase** by `execute()`; register the uppercase form.
- Return bytes. Never write to the socket from a handler — it does not have one,
  which is what makes handlers testable in isolation.

## 3. New data type or access pattern → extend Store, not the handler

`app/store.py` owns expiry and type rules. A list is returned by reference so
appending in place preserves the key's TTL. If a command needs an access pattern
the store lacks (a range, a pop), add a method there rather than reaching into
the entry from `commands.py`.

Watch two behaviours that are easy to get wrong:

- **An expired key must look absent**, not empty — go through `Store.get`, which
  deletes on read.
- **A command that empties a list must delete the key.** In Redis an empty list
  does not exist: `LLEN` on it is 0, `TYPE` is `none`, and `GET` returns null
  rather than a wrong-type error. `Store.pop_left` is the precedent — it deletes
  the entry when the last element goes; any other removing command must too.

## 4. Missing encoders

`app/resp.py` has simple strings, errors, integers, bulk strings, arrays, and the
two null forms (`NULL`, `NULL_ARRAY`). `array()` bulk-encodes every element, which
suits the list commands; an array holding mixed reply types needs assembling by
hand. Any other RESP type a new command needs belongs in `resp.py`, not in the
handler.

## 5. Verify

Use the **verify-server** skill. Cover, at minimum: the happy path, wrong arity,
the command against a key of the wrong type, a missing key, and — when the
command takes options — each error case, asserting the keyspace was not modified.
