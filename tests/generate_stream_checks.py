"""Regenerate the stream query fixtures: xrange.json and xread.json.

    python tests/generate_stream_checks.py

The RESP is computed rather than written out: these replies nest arrays two and
three deep, where a miscounted $<n> is easy to write and almost invisible to
read. Edit this file and rerun it rather than editing the JSON by hand.
"""

import json

INVALID = "-ERR Invalid stream ID specified as stream command argument\r\n"
WRONGTYPE = "-WRONGTYPE Operation against a key holding the wrong kind of value\r\n"
XRANGE_ARITY = "-ERR wrong number of arguments for 'xrange' command\r\n"
XREAD_ARITY = "-ERR wrong number of arguments for 'xread' command\r\n"
SYNTAX = "-ERR syntax error\r\n"
UNBALANCED = (
    "-ERR Unbalanced XREAD list of streams: "
    "for each stream key an ID or '$' must be specified.\r\n"
)
NULL_ARRAY = "*-1\r\n"


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


def read(key, entry_id, expect, label):
    return {"send": ["XREAD", "STREAMS", key, entry_id], "expect": expect, "label": label}


def streams_reply(*pairs):
    """The XREAD reply for several streams, each pair a (key, entries) tuple."""
    reported = "".join(
        f"*2\r\n{bulk(key)}*{len(entries)}\r\n" + "".join(entries)
        for key, entries in pairs
    )
    return f"*{len(pairs)}\r\n" + reported


def stream_reply(key, *entries):
    """The XREAD reply for one stream: [[key, [entries...]]]."""
    return streams_reply((key, entries))


def read_many(keys, ids, expect, label):
    return {"send": ["XREAD", "STREAMS", *keys, *ids], "expect": expect, "label": label}


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
    query("s", "--", "9", INVALID, "only a single dash is the minimum"),
    query("s", " -", "9", INVALID, "no whitespace around it"),
]

# "+" runs to the last entry, and is the end bound only.
checks += [
    query("s", "-", "+", reply(e50, e51, e59, e60, e75), "- to + is the whole stream"),
    query("s", "5", "+", reply(e50, e51, e59, e60, e75), "a bare start through to +"),
    query("s", "5-1", "+", reply(e51, e59, e60, e75), "an explicit start through to +"),
    query("s", "6", "+", reply(e60, e75), "+ from a later millisecond"),
    query("s", "7-6", "+", reply(), "+ from past the last entry is empty"),
    query("s", "99999", "+", reply(), "+ from a millisecond beyond the stream"),
    query("big", "-", "+", reply(entry("99999999999999999999-0", "f", "v"),
                                 entry("99999999999999999999-1", "f", "v")),
          "+ reaches 20-digit ids, which no fixed maximum would cover"),
    query("nokey", "-", "+", reply(), "+ on a missing key"),
    query("s", "notanid", "+", INVALID, "+ does not excuse a malformed start"),
    query("s", "+", "9", INVALID,
          "+ as a start is refused: unbounded ids have no largest id to bound by"),
    query("s", "+", "+", INVALID, "including + to +"),
    query("s", "-", "++", INVALID, "only a single plus is the maximum"),
    query("s", "-", "+ ", INVALID, "no whitespace around it"),
    query("s", "-", "1+", INVALID, "nor a plus after digits"),
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
    query("str", "-", "+", WRONGTYPE, "and so does - to +"),
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
    {"send": ["XRANGE"], "expect": XRANGE_ARITY, "label": "no arguments"},
    {"send": ["XRANGE", "s"], "expect": XRANGE_ARITY, "label": "key only"},
    {"send": ["XRANGE", "s", "0"], "expect": XRANGE_ARITY, "label": "no end"},
    {"send": ["XRANGE", "s", "0", "9", "COUNT", "1"], "expect": XRANGE_ARITY,
     "label": "COUNT is not supported yet"},
    query("s", "6", "6", reply(e60), "the stream still reads correctly after the errors"),
    {"send": ["XRANGE", "s", "6", "6"], "client": "b", "expect": reply(e60),
     "label": "a second client sees the same entries"},
]

# --- XREAD ------------------------------------------------------------------

r50 = entry("5-0", "a", "1")
r51 = entry("5-1", "b", "2")
r60 = entry("6-0", "c", "3")

XREAD_CHECKS = [
    add("s", "5-0", "a", "1", label="setup: 5-0"),
    add("s", "5-1", "b", "2", label="setup: 5-1"),
    add("s", "6-0", "c", "3", label="setup: 6-0"),

    read("s", "0", stream_reply("s", r50, r51, r60), "everything after 0-0"),
    read("s", "0-0", stream_reply("s", r50, r51, r60), "the same id written in full"),
    read("s", "5-0", stream_reply("s", r51, r60), "exclusive: the id given is not returned"),
    read("s", "5", stream_reply("s", r51, r60), "a bare millisecond means its sequence 0"),
    read("s", "5-1", stream_reply("s", r60), "reading on from the last id seen"),
    read("s", "4-9", stream_reply("s", r50, r51, r60), "an id before the stream starts"),
    read("s", "6-0", NULL_ARRAY, "nothing after the last entry is a null array"),
    read("s", "99999", NULL_ARRAY, "nor is anything after a later millisecond"),
    read("s", "6", NULL_ARRAY,
         "a bare millisecond means -0, which exclusivity then leaves out"),

    {"send": ["xread", "streams", "s", "5-1"], "expect": stream_reply("s", r60),
     "label": "verb and STREAMS are both case-insensitive"},

    add("dup", "1-1", "a", "1", "a", "2", label="setup: a repeated field name"),
    read("dup", "0", stream_reply("dup", entry("1-1", "a", "1", "a", "2")),
         "fields keep their order inside the reply"),
    add("bin", "1-1", "f\r\nx", "a\x00b", label="setup: binary field and value"),
    read("bin", "0", stream_reply("bin", entry("1-1", "f\r\nx", "a\x00b")),
         "binary-safe through the reply"),
    add("big", "99999999999999999999-0", "f", "v", label="setup: a 20-digit id"),
    read("big", "99999999999999999998", stream_reply("big", entry("99999999999999999999-0", "f", "v")),
         "ids past 64 bits compare as numbers"),

    read("nokey", "0", NULL_ARRAY, "a missing key reads as a null array"),
    {"send": ["TYPE", "nokey"], "expect": "+none\r\n", "label": "and XREAD did not create it"},

    {"send": ["SET", "str", "hello"], "expect": "+OK\r\n", "label": "setup: a string"},
    read("str", "0", WRONGTYPE, "XREAD on a string"),
    read("str", "notanid", INVALID, "a malformed id is reported before the type"),
    {"send": ["RPUSH", "list", "a"], "expect": ":1\r\n", "label": "setup: a list"},
    read("list", "0", WRONGTYPE, "XREAD on a list"),

    read("s", "notanid", INVALID, "letters are not an id"),
    read("s", "1-x", INVALID, "nor a non-numeric sequence"),
    read("s", "1-1-1", INVALID, "nor too many parts"),
    read("s", "", INVALID, "nor an empty id"),
    read("s", "-", INVALID, "- is an XRANGE bound, not an XREAD id"),
    read("s", "+", INVALID, "and so is +"),
    read("s", "$", INVALID, "$ is not supported yet"),

    {"send": ["XREAD"], "expect": XREAD_ARITY, "label": "no arguments"},
    {"send": ["XREAD", "STREAMS"], "expect": XREAD_ARITY, "label": "STREAMS with nothing after it"},
    {"send": ["XREAD", "STREAMS", "s"], "expect": XREAD_ARITY, "label": "a key with no id"},
    {"send": ["XREAD", "s", "0", "0"], "expect": SYNTAX, "label": "STREAMS is required"},
    {"send": ["XREAD", "COUNT", "2", "STREAMS", "s", "0"], "expect": SYNTAX,
     "label": "COUNT is not supported yet"},
    {"send": ["XREAD", "BLOCK", "0", "STREAMS", "s", "0"],
     "expect": stream_reply("s", r50, r51, r60),
     "label": "BLOCK is accepted and returns at once when there is data"},
    {"send": ["XREAD", "STREAMS", "s", "dup", "0"], "expect": UNBALANCED,
     "label": "two keys and one id is unbalanced"},

    # Several streams at once, reported in the order they were asked for.
    add("t", "1-1", "x", "9", label="setup: a second stream"),
    add("t", "2-2", "y", "8", label="setup: and a second entry in it"),
    read_many(["s", "t"], ["0", "0"],
              streams_reply(("s", [r50, r51, r60]),
                            ("t", [entry("1-1", "x", "9"), entry("2-2", "y", "8")])),
              "two streams come back in the order given"),
    read_many(["t", "s"], ["0", "0"],
              streams_reply(("t", [entry("1-1", "x", "9"), entry("2-2", "y", "8")]),
                            ("s", [r50, r51, r60])),
              "asking in the other order reverses the reply"),
    read_many(["s", "t"], ["5-1", "1-1"],
              streams_reply(("s", [r60]), ("t", [entry("2-2", "y", "8")])),
              "each stream is read from its own id"),
    read_many(["s", "t"], ["9-9", "0"],
              streams_reply(("t", [entry("1-1", "x", "9"), entry("2-2", "y", "8")])),
              "a stream with nothing new is left out, not reported empty"),
    read_many(["s", "t"], ["0", "9-9"],
              streams_reply(("s", [r50, r51, r60])),
              "including when it is the last one asked for"),
    read_many(["s", "t"], ["9-9", "9-9"], NULL_ARRAY,
              "nothing new anywhere is the null array"),
    read_many(["s", "nokey"], ["0", "0"], streams_reply(("s", [r50, r51, r60])),
              "a missing key is simply absent from the reply"),
    read_many(["nokey", "missing"], ["0", "0"], NULL_ARRAY, "all keys missing"),
    read_many(["s", "s"], ["0", "5-1"],
              streams_reply(("s", [r50, r51, r60]), ("s", [r60])),
              "the same key twice is read twice, at each id"),
    read_many(["s", "t", "dup"], ["9-9", "0", "0"],
              streams_reply(("t", [entry("1-1", "x", "9"), entry("2-2", "y", "8")]),
                            ("dup", [entry("1-1", "a", "1", "a", "2")])),
              "three streams with an empty one in the middle"),

    read_many(["s", "str"], ["0", "0"], WRONGTYPE,
              "a wrong type anywhere fails the whole read"),
    read_many(["s", "t"], ["0", "notanid"], INVALID,
              "and so does a malformed id in the second position"),
    read_many(["s", "t", "dup"], ["0", "0"], UNBALANCED,
              "three keys and two ids is unbalanced"),
    read_many(["s", "t"], ["0", "0", "0"], UNBALANCED, "as is two keys and three ids"),

    read("s", "5-1", stream_reply("s", r60), "the stream still reads after the errors"),
    {"send": ["XREAD", "STREAMS", "s", "5-1"], "client": "b", "expect": stream_reply("s", r60),
     "label": "a second client reads the same entries"},
]


# --- XREAD BLOCK ------------------------------------------------------------

b11 = entry("1-1", "a", "1")
b22 = entry("2-2", "b", "2")
b33 = entry("3-3", "c", "3")
b44 = entry("4-4", "d", "4")

BLOCK_CHECKS = [
    add("s", "1-1", "a", "1", label="setup: one entry"),

    read_many(["s"], ["0"], stream_reply("s", b11), "a plain read still works"),
    {"send": ["XREAD", "BLOCK", "100", "STREAMS", "s", "0"], "expect": stream_reply("s", b11),
     "label": "BLOCK returns at once when there is already data"},
    {"send": ["XREAD", "BLOCK", "0", "STREAMS", "s", "0"], "expect": stream_reply("s", b11),
     "label": "and BLOCK 0 does not wait either"},

    {"send": ["XREAD", "BLOCK", "0", "STREAMS", "s", "1-1"], "client": "b", "read": False,
     "label": "a client blocks with no timeout"},
    {"recv": "b", "timeout": 0.3, "expect": "", "label": "and is still parked"},
    add("s", "2-2", "b", "2", label="a write arrives"),
    {"recv": "b", "expect": stream_reply("s", b22), "label": "which wakes it with just that entry"},

    {"send": ["XREAD", "BLOCK", "80", "STREAMS", "s", "9-9"], "client": "c", "read": False,
     "label": "a client blocks with a timeout"},
    {"recv": "c", "timeout": 1.0, "expect": NULL_ARRAY, "label": "and times out with a null array"},

    {"send": ["XREAD", "BLOCK", "0", "STREAMS", "s", "2-2"], "client": "d", "read": False,
     "label": "two clients block on the same stream"},
    {"send": ["XREAD", "BLOCK", "0", "STREAMS", "s", "2-2"], "client": "e", "read": False,
     "label": "the second of them"},
    add("s", "3-3", "c", "3", label="one write arrives"),
    {"recv": "d", "expect": stream_reply("s", b33), "label": "and wakes both: the first"},
    {"recv": "e", "expect": stream_reply("s", b33),
     "label": "and the second, unlike BLPOP where one push wakes one client"},

    {"send": ["XREAD", "BLOCK", "0", "STREAMS", "s", "3-3"], "client": "f", "read": False,
     "label": "a client blocks on s"},
    add("other", "1-1", "x", "9", label="a write to a different stream"),
    {"recv": "f", "timeout": 0.3, "expect": "", "label": "leaves it parked"},
    add("s", "4-4", "d", "4", label="a write to its own stream"),
    {"recv": "f", "expect": stream_reply("s", b44), "label": "wakes it"},

    {"send": ["XREAD", "BLOCK", "0", "STREAMS", "s", "t", "9-9", "0"], "client": "g",
     "read": False, "label": "blocking on two streams at once"},
    {"recv": "g", "timeout": 0.3, "expect": "", "label": "with neither having anything"},
    add("t", "1-1", "y", "7", label="a write to the second stream"),
    {"recv": "g", "expect": stream_reply("t", entry("1-1", "y", "7")),
     "label": "reports only the stream that had something"},

    {"send": ["XREAD", "BLOCK", "-1", "STREAMS", "s", "0"],
     "expect": "-ERR timeout is negative\r\n", "label": "a negative timeout"},
    {"send": ["XREAD", "BLOCK", "abc", "STREAMS", "s", "0"],
     "expect": "-ERR timeout is not an integer or out of range\r\n",
     "label": "a non-numeric timeout"},
    {"send": ["XREAD", "BLOCK", "1.5", "STREAMS", "s", "0"],
     "expect": "-ERR timeout is not an integer or out of range\r\n",
     "label": "BLOCK is whole milliseconds, unlike BLPOP's seconds"},
    {"send": ["XREAD", "BLOCK", "STREAMS", "s", "0"],
     "expect": "-ERR timeout is not an integer or out of range\r\n",
     "label": "BLOCK with no timeout eats the next word"},
    {"send": ["XREAD", "BLOCK", "100"], "expect": XREAD_ARITY, "label": "BLOCK with no streams"},
    {"send": ["XREAD", "BLOCK", "100", "STREAMS"], "expect": SYNTAX,
     "label": "STREAMS with nothing after it"},
    {"send": ["XREAD", "BLOCK", "100", "STREAMS", "s"], "expect": UNBALANCED,
     "label": "a key with no id"},
    {"send": ["XREAD", "BLOCK", "100", "STREAMS", "s", "notanid"], "expect": INVALID,
     "label": "a malformed id is refused rather than blocked on"},
    {"send": ["XREAD", "STREAMS", "s", "0", "BLOCK", "100"], "expect": INVALID,
     "label": "after STREAMS everything is a key or an id, so BLOCK there is not one"},
    {"send": ["XREAD", "COUNT", "1", "BLOCK", "100", "STREAMS", "s", "0"], "expect": SYNTAX,
     "label": "COUNT is still not supported"},

    {"send": ["SET", "str", "hello"], "expect": "+OK\r\n", "label": "setup: a string"},
    {"send": ["XREAD", "BLOCK", "100", "STREAMS", "str", "0"], "expect": WRONGTYPE,
     "label": "a wrong type is reported rather than blocked on"},

    read_many(["s"], ["0"], stream_reply("s", b11, b22, b33, b44),
              "the stream reads back in full at the end"),
]


def write(name, checks):
    """One check per line, like the hand-written fixtures."""
    lines = ",\n".join("  " + json.dumps(check) for check in checks)
    with open(f"tests/checks/{name}.json", "w") as handle:
        handle.write("[\n" + lines + "\n]\n")
    print(f"wrote {len(checks)} checks to tests/checks/{name}.json")


write("xrange", checks)
write("xread", XREAD_CHECKS)
write("xread_block", BLOCK_CHECKS)
