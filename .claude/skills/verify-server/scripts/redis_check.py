#!/usr/bin/env python3
"""Exercise the bedis server over RESP and report pass/fail per check.

Usage:
    python .claude/skills/verify-server/scripts/redis_check.py checks.json
    ... | python .claude/skills/verify-server/scripts/redis_check.py

The checks file is a JSON list. Each entry is one of:

    {"send": ["SET", "k", "v"], "expect": "+OK\\r\\n"}   send a command, compare the reply
    {"send": ["GET", "k"]}                              send it, just print the reply
    {"sleep": 0.15}                                     pause (for expiry checks)
    {"raw": "*x\\r\\n", "expect": "-ERR Protocol error\\r\\n"}   send raw bytes
    {"send": ["GET", "k"], "client": "b"}               use a second connection

Optional "label" names the check in the output. Exits 0 only if every check with
an "expect" passed, so it can gate a commit.
"""

import argparse
import json
import socket
import sys
import time


def encode(args):
    """Build a RESP array of bulk strings."""
    out = b"*%d\r\n" % len(args)
    for arg in args:
        raw = arg.encode() if isinstance(arg, str) else bytes(arg)
        out += b"$%d\r\n%s\r\n" % (len(raw), raw)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checks", nargs="?", help="JSON file (default: stdin)")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=6389)
    parser.add_argument("--timeout", type=float, default=2.0)
    options = parser.parse_args()

    with open(options.checks) if options.checks else sys.stdin as handle:
        checks = json.load(handle)

    connections = {}

    def connect(name):
        if name not in connections:
            connections[name] = socket.create_connection(
                (options.host, options.port), timeout=options.timeout
            )
        return connections[name]

    tested = passed = 0
    for check in checks:
        if "sleep" in check:
            time.sleep(check["sleep"])
            continue

        if "raw" in check:
            payload = check["raw"].encode("latin-1")
            label = check.get("label") or repr(check["raw"])
        else:
            payload = encode(check["send"])
            label = check.get("label") or " ".join(check["send"])

        connection = connect(check.get("client", "default"))
        connection.sendall(payload)
        try:
            got = connection.recv(65536)
        except (TimeoutError, socket.timeout):
            got = b""

        if "expect" not in check:
            print(f"  --  {label:<44} {got!r}")
            continue

        want = check["expect"].encode("latin-1")
        tested += 1
        if got == want:
            passed += 1
            print(f"PASS  {label:<44} {got!r}")
        else:
            print(f"FAIL  {label:<44} {got!r}")
            print(f"      expected {want!r}")

    for connection in connections.values():
        connection.close()

    print(f"\n{passed}/{tested} checks passed")
    return 0 if passed == tested else 1


if __name__ == "__main__":
    sys.exit(main())
