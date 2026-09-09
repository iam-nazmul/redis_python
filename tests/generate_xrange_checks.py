"""Regenerate tests/checks/xrange.json.

    python tests/generate_xrange_checks.py

The RESP is computed rather than written out: XRANGE replies are arrays of
arrays, where a miscounted $<n> is easy to write and almost invisible to read.
Edit this file and rerun it rather than editing the JSON by hand.
"""

import json

INVALID = "-ERR Invalid stream ID specified as stream command argument\r\n"
WRONGTYPE = "-WRONGTYPE Operation against a key holding the wrong kind of value\r\n"
ARITY = "-ERR wrong number of arguments for 'xrange' command\r\n"


def bulk(value):
    return f"${len(value.encode('latin-1'))}\r\n{value}\r\n"


def entry(entry_id, *fields):
    """One XRANGE element: the id, then a flat array of field/value pairs."""
    inner = "".join(bulk(f) for f in fields)
    return f"*2\r\n{bulk(entry_id)}*{len(fields)}\r\n{inner}"


def reply(*entries):
    return f"*{len(entries)}\r\n" + "".join(entries)


def add(key, entry_id, *fields, label):
    return {"send": ["XADD", key, entry_id, *fields], "expect": bulk(entry_id), "label": label}


def query(key, start, end, expect, label):
    return {"send": ["XRANGE", key, start, end], "expect": expect, "label": label}


checks = []

# The example from the stage description, byte for byte.
checks += [
    add("some_key", "1526985054069-0", "temperature", "36", "humidity", "95",
        label="setup: the first entry from the stage example"),
    add("some_key", "1526985054079-0", "temperature", "37", "humidity", "94",
        label="setup: the second"),
    query("some_key", "1526985054069", "1526985054079",
          reply(entry("1526985054069-0", "temperature", "36", "humidity", "95"),
                entry("1526985054079-0", "temperature", "37", "humidity", "94")),
          "the stage example, as an array of arrays"),
]

# A stream with several sequences inside one millisecond.
e50 = entry("5-0", "a", "1")
e51 = entry("5-1", "b", "2")
e59 = entry("5-9", "c", "3")
e60 = entry("6-0", "d", "4")
e75 = entry("7-5", "e", "5")
checks += [
    add("s", "5-0", "a", "1", label="setup: 5-0"),
    add("s", "5-1", "b", "2", label="setup: 5-1"),
    add("s", "5-9", "c", "3", label="setup: 5-9"),
    add("s", "6-0", "d", "4", label="setup: 6-0"),
    add("s", "7-5", "e", "5", label="setup: 7-5"),

    query("s", "5", "7", reply(e50, e51, e59, e60, e75),
          "a bare end millisecond covers every sequence in it"),
    query("s", "5", "6", reply(e50, e51, e59, e60), "up to the end of millisecond 6"),
    query("s", "5-1", "5-9", reply(e51, e59), "both ends explicit and inclusive"),
    query("s", "5-1", "5", reply(e51, e59), "explicit start, bare end, same millisecond"),
    query("s", "5-0", "5-0", reply(e50), "a single-entry range"),
    query("s", "6", "6", reply(e60), "one millisecond only"),
    query("s", "5-2", "5-8", reply(), "a gap between entries is an empty array"),
    query("s", "7", "5", reply(), "start after end is empty, not an error"),
    query("s", "4", "4", reply(), "a millisecond with no entries"),
    query("s", "0", "99999", reply(e50, e51, e59, e60, e75), "a range wider than the stream"),
    query("s", "0-0", "0-0", reply(), "the smallest range there is"),
    {"send": ["xrange", "s", "6", "6"], "expect": reply(e60), "label": "verb is case-insensitive"},
]

# Field order and repeats survive the round trip, and values are binary-safe.
checks += [
    add("dup", "1-1", "a", "1", "a", "2", "b", "3", label="setup: a repeated field name"),
    query("dup", "1", "1", reply(entry("1-1", "a", "1", "a", "2", "b", "3")),
          "repeated fields are kept in order, not merged"),
    add("bin", "1-1", "f\r\nx", "a\x00b", label="setup: binary field and value"),
    query("bin", "1", "1", reply(entry("1-1", "f\r\nx", "a\x00b")),
          "binary-safe through the reply"),
]

# Ids past 64 bits, where a string comparison of the bounds would go wrong.
checks += [
    add("big", "99999999999999999999-0", "f", "v", label="setup: a 20-digit id"),
    add("big", "99999999999999999999-1", "f", "v", label="setup: and its next sequence"),
    query("big", "99999999999999999999", "99999999999999999999",
          reply(entry("99999999999999999999-0", "f", "v"),
                entry("99999999999999999999-1", "f", "v")),
          "bounds are compared as numbers past 64 bits"),
    query("big", "9-0", "10-0", reply(), "a nearby-looking string range matches nothing"),
]

# "-" is the smallest id there is, and stands in for either bound.
checks += [
    query("s", "-", "7", reply(e50, e51, e59, e60, e75), "- starts at the first entry"),
    query("s", "-", "5", reply(e50, e51, e59), "- with a bare end millisecond"),
    query("s", "-", "5-1", reply(e50, e51), "- with an explicit end"),
    query("s", "-", "5-0", reply(e50), "- up to the first entry itself"),
    query("s", "-", "4", reply(), "- to before the first entry is empty"),
    query("s", "-", "-", reply(), "- to - is empty: no entry can be 0-0 or below"),
    query("s", "0", "-", reply(), "- as the end alone, which Redis also allows"),
    query("big", "-", "99999999999999999999",
          reply(entry("99999999999999999999-0", "f", "v"),
                entry("99999999999999999999-1", "f", "v")),
          "- reaches a 20-digit first entry"),
    query("nokey", "-", "9", reply(), "- on a missing key"),
    query("s", "-", "notanid", INVALID, "- does not excuse a malformed end"),
    query("s", "-", "+", INVALID, "+ is not accepted yet"),
    query("s", "+", "9", INVALID, "nor as a start"),
    query("s", "--", "9", INVALID, "only a single dash is the minimum"),
    query("s", " -", "9", INVALID, "no whitespace around it"),
]

# A missing key is an empty array, and querying must not create it.
checks += [
    query("nokey", "0", "9", reply(), "a missing key is an empty array"),
    {"send": ["TYPE", "nokey"], "expect": "+none\r\n", "label": "and XRANGE did not create it"},
]

# Wrong type, and malformed bounds ahead of it.
checks += [
    {"send": ["SET", "str", "hello"], "expect": "+OK\r\n", "label": "setup: a string"},
    query("str", "0", "9", WRONGTYPE, "XRANGE on a string"),
    query("str", "notanid", "9", INVALID, "a malformed start is reported before the type"),
    query("str", "0", "notanid", INVALID, "and so is a malformed end"),
    query("str", "-", "9", WRONGTYPE, "- on a string still reports the type"),
    {"send": ["RPUSH", "list", "a"], "expect": ":1\r\n", "label": "setup: a list"},
    query("list", "0", "9", WRONGTYPE, "XRANGE on a list"),
]

for bad, label in [
    ("notanid", "letters"),
    ("1-x", "a non-numeric sequence"),
    ("-1", "an empty millisecond"),
    ("1-1-1", "too many parts"),
    ("", "an empty id"),
    ("*", "a star is not a query bound"),
    ("1 -1", "whitespace"),
]:
    checks.append(query("s", bad, "9", INVALID, f"invalid start: {label}"))
    checks.append(query("s", "0", bad, INVALID, f"invalid end: {label}"))

checks += [
    {"send": ["XRANGE"], "expect": ARITY, "label": "no arguments"},
    {"send": ["XRANGE", "s"], "expect": ARITY, "label": "key only"},
    {"send": ["XRANGE", "s", "0"], "expect": ARITY, "label": "no end"},
    {"send": ["XRANGE", "s", "0", "9", "COUNT", "1"], "expect": ARITY,
     "label": "COUNT is not supported yet"},
    query("s", "6", "6", reply(e60), "the stream still reads correctly after the errors"),
    {"send": ["XRANGE", "s", "6", "6"], "client": "b", "expect": reply(e60),
     "label": "a second client sees the same entries"},
]

# One check per line, like the hand-written fixtures.
lines = ",\n".join("  " + json.dumps(check) for check in checks)
with open("tests/checks/xrange.json", "w") as handle:
    handle.write("[\n" + lines + "\n]\n")
print(f"wrote {len(checks)} checks")
