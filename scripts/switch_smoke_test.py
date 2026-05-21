"""Fake Switch — drives the SMOClient's SwitchServer end-to-end over loopback.

Usage (assumes SMOClient is already running on localhost:17777, launched
either via the Archipelago Launcher's "SMO Client" button or directly):
    python scripts/switch_smoke_test.py [--device-id NAME]

Sequence:
  1. Connect to localhost:17777
  2. Send HELLO (with optional device_id so the multi-Switch selector
     can disambiguate this instance from others)
  3. Print every message the client sends back
  4. Every 5s, send a synthetic check (cycles through a small canned list)

Multi-Switch testing: run two instances with distinct --device-id
values in parallel. The first to HELLO becomes the active Switch (and
sees the post-HELLO replay + canned checks roundtrip); the second is
accepted as inactive (`KickMsg(reason="inactive")`) and its telemetry
is dropped by the bridge until the user promotes it via the Switches
popup. Use this to exercise the active-toggle path without booting SMO.

Stop with Ctrl-C.
"""

from __future__ import annotations

import argparse
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


async def writer_loop(writer: asyncio.StreamWriter, device_id: str = "") -> None:
    hello: dict = {"t": "hello", "mod_ver": "fake-0.0.1", "smo_ver": "1.3.0",
                   "cap_table_hash": "sha1:fake"}
    if device_id:
        hello["device_id"] = device_id
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


async def main(device_id: str = "") -> int:
    print(f"[fake-switch] connecting to {HOST}:{PORT} as device_id={device_id!r}")
    reader, writer = await asyncio.open_connection(HOST, PORT)
    try:
        await asyncio.gather(
            reader_loop(reader),
            writer_loop(writer, device_id=device_id),
        )
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Fake-Switch driver for SMOClient's SwitchServer.",
    )
    p.add_argument(
        "--device-id", default="",
        help=(
            "Optional stable identifier sent in HELLO. The bridge keys "
            "connected Switches by this value in its multi-Switch "
            "selector popup. Default empty (bridge synthesizes from peer "
            "IP). Run two instances with different IDs to exercise the "
            "selector path."
        ),
    )
    return p.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    try:
        sys.exit(asyncio.run(main(device_id=args.device_id)))
    except KeyboardInterrupt:
        sys.exit(130)
    except ConnectionRefusedError:
        print(f"[fake-switch] connection refused — is the bridge running on {HOST}:{PORT}?")
        sys.exit(2)
