"""Fake Switch — drives the bridge end-to-end over loopback.

Usage (assumes bridge is already running on localhost:17777):
    python scripts/bridge_smoke_test.py

Sequence:
  1. Connect to localhost:17777
  2. Send HELLO
  3. Print every message the bridge sends back
  4. Every 5s, send a synthetic check (cycles through a small canned list)

Stop with Ctrl-C.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from itertools import cycle

HOST = "127.0.0.1"
PORT = 17777

CANNED_CHECKS = [
    {"t": "check", "kind": "moon",    "kingdom": "Cap",     "shine_id": "Frog-Jumping Above the Fog"},
    {"t": "check", "kind": "moon",    "kingdom": "Cascade", "shine_id": "Our First Power Moon"},
    {"t": "check", "kind": "capture", "cap": "Paragoomba"},
    {"t": "check", "kind": "moon",    "kingdom": "Sand",    "shine_id": "Atop the Highest Tower"},
]


async def reader_loop(reader: asyncio.StreamReader) -> None:
    buf = bytearray()
    while True:
        chunk = await reader.read(4096)
        if not chunk:
            print("[fake-switch] EOF from bridge")
            return
        buf.extend(chunk)
        while True:
            nl = buf.find(b"\n")
            if nl < 0:
                break
            line = bytes(buf[:nl]).strip()
            del buf[: nl + 1]
            if line:
                try:
                    msg = json.loads(line)
                    print(f"<< {msg}")
                except Exception as e:
                    print(f"[fake-switch] parse error: {e!r} on {line[:200]!r}")


async def writer_loop(writer: asyncio.StreamWriter) -> None:
    hello = {"t": "hello", "mod_ver": "fake-0.0.1", "smo_ver": "1.3.0",
             "cap_table_hash": "sha1:fake"}
    writer.write((json.dumps(hello) + "\n").encode("utf-8"))
    await writer.drain()
    print(f">> {hello}")

    cycler = cycle(CANNED_CHECKS)
    while True:
        await asyncio.sleep(5.0)
        msg = next(cycler)
        writer.write((json.dumps(msg) + "\n").encode("utf-8"))
        await writer.drain()
        print(f">> {msg}")
        # Every 30s also send a ping to validate pong.
        if int(time.time()) % 30 < 5:
            ping = {"t": "ping", "ts_ms": int(time.time() * 1000)}
            writer.write((json.dumps(ping) + "\n").encode("utf-8"))
            await writer.drain()
            print(f">> {ping}")


async def main() -> int:
    print(f"[fake-switch] connecting to {HOST}:{PORT}")
    reader, writer = await asyncio.open_connection(HOST, PORT)
    try:
        await asyncio.gather(reader_loop(reader), writer_loop(writer))
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
    except ConnectionRefusedError:
        print(f"[fake-switch] connection refused — is the bridge running on {HOST}:{PORT}?")
        sys.exit(2)
