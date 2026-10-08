#!/usr/bin/env python3
"""Dev/debug CLI: open a /ws connection and register as a role, without a browser.

Prints every broadcast state as it arrives. Reads commands from stdin:
  message <asset>
  set_mode <MODE> [delay_ms] [effect]
  quit

If the controller isn't already running at a localhost URL, this starts it
(detached, so it keeps running after this script exits) and waits for it to
come up before connecting. Auto-started, it binds --host 0.0.0.0 by default
so commander/receiver on other devices can reach it too; override with
--bind-host if you want it local-only instead.

Examples (use .venv's python directly so it doesn't need to be activated first):
  .venv/bin/python tools/ws_client.py operator
  .venv/bin/python tools/ws_client.py commander --url ws://localhost:8000/ws
  .venv/bin/python tools/ws_client.py operator --bind-host 127.0.0.1
"""
import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import websockets

DEFAULT_URL = "ws://localhost:8000/ws"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCAL_HOSTS = {"localhost", "127.0.0.1"}


def parse_command(line: str) -> dict | None:
    parts = line.strip().split()
    if not parts:
        return None
    cmd = parts[0]
    if cmd == "message" and len(parts) == 2:
        return {"type": "message", "asset": parts[1]}
    if cmd == "set_mode" and len(parts) >= 2:
        msg = {"type": "set_mode", "mode": parts[1]}
        if len(parts) >= 3:
            msg["delay_ms"] = int(parts[2])
        if len(parts) >= 4:
            msg["effect"] = parts[3]
        return msg
    print(f"unrecognized command: {line!r}", file=sys.stderr)
    return None


async def read_stdin_lines():
    loop = asyncio.get_event_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            return
        yield line


async def receive_loop(ws):
    async for raw in ws:
        data = json.loads(raw)
        print(json.dumps(data, indent=2))


async def send_loop(ws):
    async for line in read_stdin_lines():
        if line.strip() == "quit":
            return
        message = parse_command(line)
        if message is not None:
            await ws.send(json.dumps(message))


async def wait_for_port(host: str, port: int, timeout: float) -> bool:
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        try:
            _, writer = await asyncio.open_connection(host, port)
        except OSError:
            if asyncio.get_event_loop().time() >= deadline:
                return False
            await asyncio.sleep(0.2)
            continue
        writer.close()
        return True


async def connect(url: str, bind_host: str):
    try:
        return await websockets.connect(url)
    except OSError as exc:
        host = urlparse(url).hostname
        if host not in LOCAL_HOSTS:
            raise
        print(f"controller not reachable at {url}; starting it (--host {bind_host})...", file=sys.stderr)
        subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "controller.main:app", "--host", bind_host],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        if not await wait_for_port(host, urlparse(url).port, timeout=15.0):
            raise RuntimeError(f"controller did not come up at {url} in time") from exc
        print("controller is up (running detached; it'll keep serving after this exits)", file=sys.stderr)
        return await websockets.connect(url)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=["commander", "receiver", "operator"])
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--bind-host", default="0.0.0.0", help="host to bind when auto-starting the controller (default: 0.0.0.0)")
    args = parser.parse_args()

    ws = await connect(args.url, args.bind_host)
    try:
        await ws.send(json.dumps({"type": "register", "client": args.role}))
        print(f"connected as {args.role}; type commands (message <asset> | set_mode <MODE> [delay_ms] [effect] | quit)")

        receiver = asyncio.create_task(receive_loop(ws))
        sender = asyncio.create_task(send_loop(ws))
        done, pending = await asyncio.wait({receiver, sender}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
    finally:
        await ws.close()


if __name__ == "__main__":
    asyncio.run(main())
